#!/usr/bin/env python3
"""Overlay patch 0035 (Phase 2 / stereo): the aurora stereo-3D render engine.

STEREO-3D-RECOVERY-FABLE §1. True stereo without the (impossible) fifo replay:
aurora already records each frame as a FramePacket (every render pass, in order),
so gfx::end_frame executes that packet TWICE -- LEFT then RIGHT eye -- and the whole
frame renders again (EFB copies, shadow/reflection passes, everything). The per-eye
difference is a camera-space X shift injected in the GX vertex shader via a tiny
@group(3) `stereo` uniform (e, inv_conv); at e=0 it is bit-identical to mono.

Files, one concern (the render-side engine):
  * gx/shader.cpp      -- the `stereo` uniform + the camera-space eye epilogue.
  * gx/gx.cpp          -- stereo bind-group layout in the GX pipeline layout; the
                          TEXGEN-SPLIT per-chain txs policy at the config snapshot
                          (view-projected chains opt IN to the texcoord shift; world/
                          untagged stay glued) + the 1 Hz class histogram.
  * gx/gx.hpp          -- streamed texmtx-class arrays in GXState; TcgConfig._p2 is
                          the per-chain shift-enable (rides config identity via memcmp).
  * gx/pipeline.cpp    -- bind @group(3) per GX draw (OFF/LEFT/RIGHT per execution).
  * gx/command_processor.cpp -- parse the 0x5F8 texgen-class tag; reset a slot's class
                          on any untagged matrix write to it.
  * dolphin/gx/GXTransform.cpp -- duskTagTexMtxClass(): the tag as a 1-word XF write
                          that RIDES THE FIFO adjacent to its matrix load (a global
                          would desync -- 5 drains/frame, materials queue between).
  * gfx/common.hpp     -- externs, the (eye,isLast) EndFrameCallback, stereo_set_*.
  * gfx/common.cpp     -- the buffers/bind groups, per-frame eye apply (on the render
                          worker -- WriteBuffer from main races submit -> EndBlit
                          crash, D-035), and the packet re-execution in end_frame.

aurora.cpp drives this (stereo_set_enabled/params + the per-eye callback) in patch
0032; the camera distance feeding the convergence comes from patch 0036; the J3D
tag-emission hooks (J3DTexMtxMode -> duskTagTexMtxClass) are patch 0037.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor/dusklight/extern/aurora"

# rel -> list of (old, new) sequential string replacements
EDITS = {
    "lib/gx/shader.cpp": [
        # the eye epilogue (camera-space shift, perspective only)
        ('        "\\n    let mv_pos = vec4f({}, 1.0) * ubuf.postex_mtx[in_pnmtxidx];"\n'
         '        "\\n    out.pos = vec4f(mv_pos, 1.0) * ubuf.proj;",\n'
         '        vtx_attr(config, GX_VA_POS));\n',
         '        "\\n    let mv_pos = vec4f({}, 1.0) * ubuf.postex_mtx[in_pnmtxidx];"\n'
         '        // visionOS stereo (STEREO-3D-RECOVERY-FABLE \xa71.1 + FOLLOWUP-ANSWER B): analytic\n'
         '        // per-eye disparity in clip space (== the old translate+skew for near/mid), with\n'
         '        // a tanh KNEE that soft-compresses only the far side (p>0) so distant water/sky\n'
         '        // doesn\'t push too deep and small far objects stop doubling; near/mid untouched.\n'
         '        // Perspective only (ortho/HUD has no w element -> flat on the panel). e=0 == mono.\n'
         '        "\\n    let sp_persp = (ubuf.proj[2][3] != 0.0 || ubuf.proj[3][2] != 0.0);"\n'
         '        // sp_p is hoisted to function scope so the texgen section (WATER-ANSWER fix) can\n'
         '        // reuse it to shift the projected texcoord chains by the same per-eye parallax,\n'
         '        // scaled by stereo.txs (a slider you tune -- 0 = off).\n'
         '        "\\n    out.pos = vec4f(mv_pos, 1.0) * ubuf.proj;"\n'
         '        "\\n    var sp_p = 0.0;"\n'
         '        "\\n    if (sp_persp) {{"\n'
         '        // NEAR-FIELD (D-063): softplus dz-floor ALONE (no hard knee). Floors sp_dz to ~s*ln2\n'
         '        // as dz->0 (s = kgrad_c = kGrad*C), which caps BOTH the near disparity and its GRADIENT\n'
         '        // (d/dz of 1/dz ~ 1/dz^2 explodes as dz->0 -> adjacent near verts tear apart per eye = the\n'
         '        // V-split). Smooth everywhere, so it cannot shelf into a step. Was inert until D-061\n'
         '        // removed the near knee that used to re-clamp sp_p and mask it.\n'
         '        "\\n      let sp_dz_raw = max(-mv_pos.z, 1.0);"\n'
         '        "\\n      var sp_dz = sp_dz_raw;"\n'
         '        "\\n      if (stereo.kgrad_c > 0.0) {{ sp_dz = stereo.kgrad_c * log(1.0 + exp(min(sp_dz_raw / stereo.kgrad_c, 20.0))); }}"\n'
         '        "\\n      sp_p = ubuf.proj[0][0] * (stereo.inv_conv - 1.0 / sp_dz);"\n'
         '        "\\n      let sp_pmax = stereo.kfar * ubuf.proj[0][0] * stereo.inv_conv;"\n'
         '        "\\n      if (sp_p > 0.0 && sp_pmax > 0.0) {{ sp_p = sp_pmax * tanh(sp_p / sp_pmax); }}"\n'
         '        "\\n      out.pos.x = out.pos.x + stereo.e * sp_p * out.pos.w;"\n'
         '        "\\n    }}",\n'
         '        vtx_attr(config, GX_VA_POS));\n'),
        # the WGSL uniform declaration
        ('@group(1) @binding(0)\n'
         'var<uniform> ubuf: Uniform;{1}\n',
         '@group(1) @binding(0)\n'
         'var<uniform> ubuf: Uniform;{1}\n'
         'struct Stereo {{ e: f32, inv_conv: f32, kfar: f32, flags: f32, txs: f32, knear: f32, kgrad_c: f32, sp_pad2: f32 }};\n'
         '@group(3) @binding(0)\n'
         'var<uniform> stereo: Stereo;\n'),
        # WATER-ANSWER §B1/§D probe: flag any position-sourced / projective-post-matrix
        # texcoord chain (the TP water sky/reflection shine, computed from UNSHIFTED mv_pos).
        ('  for (int i = 0; i < info.sampledTexCoords.size(); ++i) {\n'
         '    if (!info.sampledTexCoords.test(i)) {\n'
         '      continue;\n'
         '    }\n'
         '    const auto& tcg = config.tcgs[i];\n'
         '    if (tcg.type == GX_TG_MTX3x4) {\n',
         '  // TEXGEN-SPLIT probe: color by POLICY (which chains actually take the txs shift) so\n'
         '  // you can SEE the class split live -- the mode tag decided _p2 at the config snapshot.\n'
         '  // BLUE = takes the shift (ViewProjmap: the water shine), YELLOW = projective but held\n'
         '  // glued (world-projected / untagged: ground overlay, cloud + drop shadows).\n'
         '  bool probeShift = false;\n'
         '  bool probeGlued = false;\n'
         '  for (int i = 0; i < info.sampledTexCoords.size(); ++i) {\n'
         '    if (!info.sampledTexCoords.test(i)) {\n'
         '      continue;\n'
         '    }\n'
         '    const auto& tcg = config.tcgs[i];\n'
         '    if (tcg._p2 != 0) {\n'
         '      probeShift = true;\n'
         '    } else if (tcg.src == GX_TG_POS || tcg.postMtx != GX_PTIDENTITY) {\n'
         '      probeGlued = true;\n'
         '    }\n'
         '    if (tcg.type == GX_TG_MTX3x4) {\n'),
        # WATER-ANSWER §D false-color probe: tint the fragment by shift category when on.
        ('  if constexpr (EnableNormalVisualization) {\n'
         '    fragmentFn += "\\n    prev = vec4f(in.nrm, prev.a);";\n'
         '  }\n'
         '\n'
         '  const auto shaderSource = fmt::format(R"""(\n',
         '  if constexpr (EnableNormalVisualization) {\n'
         '    fragmentFn += "\\n    prev = vec4f(in.nrm, prev.a);";\n'
         '  }\n'
         '\n'
         '  // visionOS stereo probe (TEXGEN-SPLIT-ANSWER): false-color by shift POLICY. RED = ortho\n'
         '  // (HUD). GREEN = ordinary texgen (mesh disparity only). BLUE = draw has a chain taking the\n'
         '  // txs texcoord shift (ViewProjmap: water shine). YELLOW = projective chain(s) held GLUED\n'
         '  // (world-projected / untagged: terrain overlay, cloud + drop shadows). MAGENTA = both kinds\n'
         '  // on one draw. Verification: water should read BLUE, ground/shadows YELLOW; if the water\n'
         '  // reads YELLOW the mode split mis-fired -- flip "Texture depth on all layers" ON and report.\n'
         '  {\n'
         '    const char* dbgNonOrtho;\n'
         '    if (probeShift && probeGlued) {\n'
         '      dbgNonOrtho = "vec4f(1.0, 0.15, 1.0, 1.0)";    // MAGENTA: shifted + glued chains together\n'
         '    } else if (probeShift) {\n'
         '      dbgNonOrtho = "vec4f(0.15, 0.30, 1.0, 1.0)";   // BLUE: takes the txs shift (view-projected)\n'
         '    } else if (probeGlued) {\n'
         '      dbgNonOrtho = "vec4f(1.0, 1.0, 0.15, 1.0)";    // YELLOW: projective, held glued (world)\n'
         '    } else {\n'
         '      dbgNonOrtho = "vec4f(0.15, 1.0, 0.30, 1.0)";   // GREEN: ordinary texgen (mesh)\n'
         '    }\n'
         '    fragmentFn += fmt::format(\n'
         '        "\\n    if ((u32(stereo.flags) & 1u) != 0u) {{"\n'
         '        "\\n      let dbg_persp = (ubuf.proj[2][3] != 0.0 || ubuf.proj[3][2] != 0.0);"\n'
         '        "\\n      prev = select({}, vec4f(1.0, 0.15, 0.15, 1.0), !dbg_persp);"\n'
         '        "\\n    }}",\n'
         '        dbgNonOrtho);\n'
         '  }\n'
         '\n'
         '  const auto shaderSource = fmt::format(R"""(\n'),
        # WATER-ANSWER fix: shift the projected texcoord chains (position-sourced / projective
        # -- the sky/reflection shine) by the mesh's per-eye parallax, gated on sp_txs.
        ('    }\n'
         '    if (tcg.type == GX_TG_MTX3x4) {\n'
         '      vtxXfrAttrs += fmt::format("\\n    out.tex{0}_uvw = tc{0}_proj.xyz;", i);\n',
         '    }\n'
         '    // WATER-ANSWER fix, gated per-chain by TEXGEN-SPLIT-ANSWER \xa71: texcoords come from\n'
         '    // UNSHIFTED mv_pos, i.e. glued to the geometry -- CORRECT for every surface-anchored\n'
         '    // layer (terrain overlay, cloud + drop shadows; shifting them lifts shadows off the\n'
         '    // ground and tears a step where sp_p saturates). Only view-anchored chains -- the\n'
         '    // water\'s sky-shine, J3DTexMtxMode ViewProjmap, streamed via the 0x5F8 tag into\n'
         '    // tcg._p2 at the config snapshot -- opt IN to the txs shift so their feature\n'
         '    // disparity matches the mesh. e=0 => no-op in mono. Divisor matches the sample:\n'
         '    // 3x4 divides by .z in the fragment, affine 2x4 samples .xy directly.\n'
         '    if (tcg._p2 != 0) {\n'
         '      if (tcg.type == GX_TG_MTX3x4) {\n'
         '        vtxXfrAttrs += fmt::format(\n'
         '            "\\n    if (sp_persp) {{ tc{0}_proj.x = tc{0}_proj.x + stereo.e * sp_p * stereo.txs * tc{0}_proj.z; }}", i);\n'
         '      } else {\n'
         '        vtxXfrAttrs += fmt::format(\n'
         '            "\\n    if (sp_persp) {{ tc{0}_proj.x = tc{0}_proj.x + stereo.e * sp_p * stereo.txs; }}", i);\n'
         '      }\n'
         '    }\n'
         '    if (tcg.type == GX_TG_MTX3x4) {\n'
         '      vtxXfrAttrs += fmt::format("\\n    out.tex{0}_uvw = tc{0}_proj.xyz;", i);\n'),
    ],
    "lib/gx/gx.cpp": [
        ('        gfx::g_staticBindGroupLayout,\n'
         '        gfx::g_uniformBindGroupLayout,\n'
         '        sTextureBindGroupLayout,\n'
         '    };\n',
         '        gfx::g_staticBindGroupLayout,\n'
         '        gfx::g_uniformBindGroupLayout,\n'
         '        sTextureBindGroupLayout,\n'
         '        gfx::g_stereoBindGroupLayout, // @group(3): visionOS stereo eye offset\n'
         '    };\n'),
        # TEXGEN-SPLIT-ANSWER: shell toggle + probe flag externs (defined in DuskHostViewController.m)
        ('#include "gx_fmt.hpp"\n',
         '#include "gx_fmt.hpp"\n'
         '\n'
         '// dusk stereo (TEXGEN-SPLIT-ANSWER \xa71): shell toggle -- 0 (default) = only ViewProjmap-\n'
         '// tagged chains get the txs texcoord shift ("Texture depth" acts on the water alone);\n'
         '// 1 = legacy shift-every-projective-chain, kept as the A/B + recovery path.\n'
         'extern "C" volatile int gDusk3DTxsAll;\n'
         '// False-color probe flag (also gates the 1 Hz texgen-class histogram log).\n'
         'extern "C" volatile int gDusk3DStereoDebug;\n'),
        # TEXGEN-SPLIT-ANSWER §1: the per-chain policy at the config snapshot + the histogram.
        ('  for (u8 i = 0; i < g_gxState.numTexGens; ++i) {\n'
         '    config.shaderConfig.tcgs[i] = g_gxState.tcgs[i];\n'
         '  }\n',
         '  for (u8 i = 0; i < g_gxState.numTexGens; ++i) {\n'
         '    config.shaderConfig.tcgs[i] = g_gxState.tcgs[i];\n'
         '    // TEXGEN-SPLIT-ANSWER \xa71: per-chain texcoord-shift policy. Texcoords are computed\n'
         '    // from UNSHIFTED mv_pos, i.e. glued to the geometry -- correct for every surface-\n'
         '    // anchored layer (terrain overlay, cloud + drop shadows). Only view-anchored\n'
         '    // content (the water\'s sky-shine, J3DTexMtxMode ViewProjmap 3/9, streamed via the\n'
         '    // 0x5F8 tag) opts IN to the txs shift. Untagged/world chains fail SAFE (glued).\n'
         '    auto& tcg = config.shaderConfig.tcgs[i];\n'
         '    tcg._p2 = 0;\n'
         '    if (tcg.src == GX_TG_POS || tcg.postMtx != GX_PTIDENTITY) {\n'
         '      u8 cls = 0;\n'
         '      if (tcg.postMtx != GX_PTIDENTITY) {\n'
         '        const u32 idx = (static_cast<u32>(tcg.postMtx) - static_cast<u32>(GX_PTTEXMTX0)) / 3;\n'
         '        if (idx < MaxPTTexMtx) {\n'
         '          cls = g_gxState.ptTexMtxClass[idx];\n'
         '        }\n'
         '      } else if (tcg.mtx >= GX_TEXMTX0 && tcg.mtx < GX_IDENTITY) {\n'
         '        const u32 idx = (static_cast<u32>(tcg.mtx) - static_cast<u32>(GX_TEXMTX0)) / 3;\n'
         '        if (idx < MaxTexMtx) {\n'
         '          cls = g_gxState.texMtxClass[idx];\n'
         '        }\n'
         '      }\n'
         '      tcg._p2 = (gDusk3DTxsAll != 0 || cls == 3 || cls == 9) ? 1 : 0;\n'
         '      // 1 Hz class histogram while the probe is on (Fable\'s verification gate: water\n'
         '      // must read ViewProjmap 3/9; ground overlay + cloud shadows Projmap 2/8; drop\n'
         '      // shadows untagged 0). If the water logs Projmap, STOP and report (the flip\n'
         '      // needs a second look) -- the "all layers" toggle is the interim recovery.\n'
         '      if (gDusk3DStereoDebug != 0) {\n'
         '        static std::array<uint32_t, 16> s_clsCount{};\n'
         '        static uint32_t s_clsLogFrame = 0;\n'
         '        s_clsCount[cls & 15u]++;\n'
         '        const uint32_t fr = gfx::current_frame();\n'
         '        if (fr - s_clsLogFrame >= 120) {\n'
         '          s_clsLogFrame = fr;\n'
         '          static Module TcgLog("aurora::gx::tcg");\n'
         '          TcgLog.info("dusk3d-tcg: untag {} projB {} vprojB {} proj {} vproj {} other {}",\n'
         '                      s_clsCount[0], s_clsCount[2], s_clsCount[3], s_clsCount[8], s_clsCount[9],\n'
         '                      s_clsCount[1] + s_clsCount[4] + s_clsCount[5] + s_clsCount[6] +\n'
         '                          s_clsCount[7] + s_clsCount[10] + s_clsCount[11] + s_clsCount[12] +\n'
         '                          s_clsCount[13] + s_clsCount[14] + s_clsCount[15]);\n'
         '          s_clsCount = {};\n'
         '        }\n'
         '      }\n'
         '    }\n'
         '  }\n'),
    ],
    "lib/gx/gx.hpp": [
        # TcgConfig._p2 becomes the per-chain shift-enable (participates in config identity
        # via the memcmp operator== -- distinct pipelines per policy, exactly what we want).
        ('  bool normalize = false;\n'
         '  u8 embossSrc = 0; // Emboss source texcoord (GX_TG_BUMP*)\n'
         '  u8 _p2 = 0;\n'
         '  u8 _p3 = 0;\n',
         '  bool normalize = false;\n'
         '  u8 embossSrc = 0; // Emboss source texcoord (GX_TG_BUMP*)\n'
         '  // dusk stereo (TEXGEN-SPLIT-ANSWER \xa71): 1 = this chain takes the stereo txs texcoord\n'
         '  // shift (ViewProjmap-tagged, or legacy-all toggle). Set at the config snapshot from the\n'
         '  // streamed class; participates in config identity via memcmp (was padding).\n'
         '  u8 _p2 = 0;\n'
         '  u8 _p3 = 0;\n'),
        # streamed texmtx-class state (written by the 0x5F8 tag parse; reset on untagged writes)
        ('  std::array<Mat3x4<float>, MaxTexMtx> texMtxs;\n'
         '  std::array<Mat3x4<float>, MaxPTTexMtx> ptTexMtxs;\n',
         '  std::array<Mat3x4<float>, MaxTexMtx> texMtxs;\n'
         '  std::array<Mat3x4<float>, MaxPTTexMtx> ptTexMtxs;\n'
         '  // dusk stereo (TEXGEN-SPLIT-ANSWER \xa71): J3DTexMtxMode class per texmtx slot, streamed\n'
         '  // via the 0x5F8 fifo tag adjacent to each matrix load (exact ordering by construction).\n'
         '  // 0 = untagged (direct-GX draws e.g. drop shadows -> glued). Reset to 0 whenever the\n'
         '  // slot\'s matrix is rewritten without a tag.\n'
         '  std::array<u8, MaxTexMtx> texMtxClass{};\n'
         '  std::array<u8, MaxPTTexMtx> ptTexMtxClass{};\n'),
    ],
    "lib/dolphin/gx/GXTransform.cpp": [
        # the tag emitter: a 1-word XF write to scratch 0x5F8, riding the fifo adjacent to
        # the matrix load it describes. Called from the J3D hook sites (patch 0037).
        ('  u32 count = (type == GX_MTX2x4) ? 8 : 12;\n'
         '  u32 reg = addr | ((count - 1) << 16);\n'
         '\n'
         '  GX_WRITE_U8(0x10);\n'
         '  GX_WRITE_U32(reg);\n'
         '\n'
         '  const auto* mtx = reinterpret_cast<const f32*>(mtx_);\n'
         '  for (u32 i = 0; i < count; i++) {\n'
         '    GX_WRITE_F32(mtx[i]);\n'
         '  }\n'
         '}\n',
         '  u32 count = (type == GX_MTX2x4) ? 8 : 12;\n'
         '  u32 reg = addr | ((count - 1) << 16);\n'
         '\n'
         '  GX_WRITE_U8(0x10);\n'
         '  GX_WRITE_U32(reg);\n'
         '\n'
         '  const auto* mtx = reinterpret_cast<const f32*>(mtx_);\n'
         '  for (u32 i = 0; i < count; i++) {\n'
         '    GX_WRITE_F32(mtx[i]);\n'
         '  }\n'
         '}\n'
         '\n'
         '// dusk stereo (TEXGEN-SPLIT-ANSWER \xa71): tag the JUST-WRITTEN texmtx slot with its\n'
         '// J3DTexMtxMode so the config snapshot can split view-projected chains (water shine,\n'
         '// takes the stereo txs shift) from world-projected ones (ground overlay, cloud shadows\n'
         '// -- glued). A 1-word XF write to scratch 0x5F8 ([0x500,0x5F0) is PT matrix data) that\n'
         '// RIDES THE FIFO adjacent to the load -- a plain global would desync: every matrix load\n'
         '// is just bytes in the stream, drained ~5x/frame, with materials queued between drains.\n'
         'extern "C" void duskTagTexMtxClass(u32 gxMtxId, u32 cls) {\n'
         '  GX_WRITE_U8(0x10);\n'
         '  GX_WRITE_U32(0x5F8);\n'
         '  GX_WRITE_U32((gxMtxId << 8) | (cls & 0xFFu));\n'
         '}\n'),
    ],
    "lib/gx/pipeline.cpp": [
        # NEAR-DOUBLING §5: extern mode selector for pinning a transparency class to the panel.
        ('namespace aurora::gx {\n'
         'static Module Log("aurora::gx");\n',
         'namespace aurora::gx {\n'
         'static Module Log("aurora::gx");\n'
         '// NEAR-DOUBLING \xa75: shell selector -- which transparency class to pin to the panel (so the\n'
         '// doubling sprites render single). 0=off 1=glows(additive+noZ) 2=no-Z-write 3=transparent\n'
         '// (blend or alpha-test) 4=alpha-tested. Defined in DuskHostViewController.m.\n'
         'extern "C" volatile int gDusk3DFlatMode;\n'
         '// Size gate: only flatten draws with <= this many vertices, so tiny sprites (butterflies,\n'
         '// flowers) get pinned but large draws of the same class (ground decals / walking paths) keep\n'
         '// their depth. 0 = no limit (flatten the whole class). Defined in DuskHostViewController.m.\n'
         'extern "C" volatile int gDusk3DFlatMaxVtx;\n'),
        ('  if (data.bindGroups.textureBindGroup) {\n'
         '    pass.SetBindGroup(2, gfx::find_bind_group(data.bindGroups.textureBindGroup));\n'
         '  }\n',
         '  if (data.bindGroups.textureBindGroup) {\n'
         '    pass.SetBindGroup(2, gfx::find_bind_group(data.bindGroups.textureBindGroup));\n'
         '  }\n'
         '  // visionOS stereo: @group(3) eye offset for the current execution (OFF/LEFT/RIGHT).\n'
         '  // NEAR-DOUBLING \xa75: pin the transparency class the Flat-mode selects to the mono (OFF,\n'
         '  // e=0) offset so those draws render single on the panel. Cycling modes finds the sprite class.\n'
         '  const uint8_t sc = data.stereoClass;\n'
         '  bool flat = false;\n'
         '  switch (gDusk3DFlatMode) {\n'
         '    case 1: flat = (sc & 2u) && (sc & 4u); break; // glows: additive (dst=ONE) + no Z-write\n'
         '    case 2: flat = (sc & 4u) != 0u; break;        // any no-Z-write\n'
         '    case 3: flat = (sc & 1u) || (sc & 8u); break; // transparent: alpha-blended or alpha-tested\n'
         '    case 4: flat = (sc & 8u) != 0u; break;        // alpha-tested (cutout)\n'
         '    case 5: flat = (sc & 4u) || (sc & 8u); break; // No-Z + cutout: butterflies AND ground specks\n'
         '    default: break;\n'
         '  }\n'
         '  // Size gate: keep the flatten to SMALL draws (butterflies/flowers), not large ones of the\n'
         '  // same class (ground decals / walking paths that share no-Z but need their depth).\n'
         '  if (flat && gDusk3DFlatMaxVtx > 0 && data.vtxCount > static_cast<uint32_t>(gDusk3DFlatMaxVtx)) {\n'
         '    flat = false;\n'
         '  }\n'
         '  const int stereoIdx = flat ? 0 : gfx::g_stereoEye;\n'
         '  pass.SetBindGroup(3, gfx::g_stereoBindGroups[stereoIdx]);\n'),
    ],
    "lib/gx/pipeline.hpp": [
        ('  GXBindGroups bindGroups;\n'
         '  uint32_t dstAlpha;\n'
         '};\n',
         '  GXBindGroups bindGroups;\n'
         '  uint32_t dstAlpha;\n'
         '  // visionOS stereo (NEAR-DOUBLING \xa75): the draw\'s transparency class, so render() can pin\n'
         '  // the doubling sprite class (butterflies/flowers/sparkles) to the panel. Which bits count\n'
         '  // as "flatten" is the Flat-mode selector (we don\'t yet know the exact sprite class).\n'
         '  // bit 1 = alpha-blended, bit 2 = additive (dst=ONE), bit 4 = no Z-write, bit 8 = alpha-test.\n'
         '  uint8_t stereoClass = 0;\n'
         '};\n'),
    ],
    "lib/gx/command_processor.cpp": [
        # TEXGEN-SPLIT-ANSWER §1: parse the 0x5F8 texgen-class tag (before the PT-matrix branch).
        ('  } else if (addr >= 0x500 && addr < 0x5F0) {\n',
         '  } else if (addr == 0x5F8) {\n'
         '    // dusk stereo (TEXGEN-SPLIT-ANSWER \xa71): texgen-class tag, emitted adjacent to its\n'
         '    // matrix load by duskTagTexMtxClass / the J3D GD hooks (patch 0037), so parse-time\n'
         '    // ordering is exact by construction. payload = (GX texmtx id << 8) | J3DTexMtxMode.\n'
         '    CHECK(len == 1, "XF: texgen-class tag bad len {}", len);\n'
         '    const u32 payload = read_u32(data, bigEndian);\n'
         '    const u32 id = payload >> 8;\n'
         '    const u32 cls = payload & 0xFFu;\n'
         '    if (id >= GX_PTTEXMTX0) {\n'
         '      const u32 idx = (id - static_cast<u32>(GX_PTTEXMTX0)) / 3;\n'
         '      if (idx < MaxPTTexMtx) {\n'
         '        g_gxState.ptTexMtxClass[idx] = static_cast<u8>(cls);\n'
         '      }\n'
         '    } else if (id >= GX_TEXMTX0) {\n'
         '      const u32 idx = (id - static_cast<u32>(GX_TEXMTX0)) / 3;\n'
         '      if (idx < MaxTexMtx) {\n'
         '        g_gxState.texMtxClass[idx] = static_cast<u8>(cls);\n'
         '      }\n'
         '    }\n'
         '    g_gxState.stateDirty = true;\n'
         '    return true;\n'
         '  } else if (addr >= 0x500 && addr < 0x5F0) {\n'),
        # reset a REGULAR texmtx slot's class on any untagged write (tag re-arms right after)
        ('    // Determine if 2x4 or 3x4 from count\n'
         '    auto& mtx = g_gxState.texMtxs[mtxIdx];\n'
         '    f32* flat = reinterpret_cast<f32*>(&mtx);\n'
         '    for (u32 i = 0; i < len; i++) {\n'
         '      flat[i] = read_f32(data + i * 4, bigEndian);\n'
         '    }\n',
         '    // Determine if 2x4 or 3x4 from count\n'
         '    auto& mtx = g_gxState.texMtxs[mtxIdx];\n'
         '    f32* flat = reinterpret_cast<f32*>(&mtx);\n'
         '    for (u32 i = 0; i < len; i++) {\n'
         '      flat[i] = read_f32(data + i * 4, bigEndian);\n'
         '    }\n'
         '    // dusk stereo: an untagged load means "not J3D-classified" -- back to unknown/glued.\n'
         '    g_gxState.texMtxClass[mtxIdx] = 0;\n'),
        # reset a PT texmtx slot's class likewise
        ('    auto& mtx = g_gxState.ptTexMtxs[mtxIdx];\n'
         '    f32* flat = reinterpret_cast<f32*>(&mtx);\n'
         '    for (u32 i = 0; i < len; i++) {\n'
         '      flat[startOffset + i] = read_f32(data + i * 4, bigEndian);\n'
         '    }\n',
         '    auto& mtx = g_gxState.ptTexMtxs[mtxIdx];\n'
         '    f32* flat = reinterpret_cast<f32*>(&mtx);\n'
         '    for (u32 i = 0; i < len; i++) {\n'
         '      flat[startOffset + i] = read_f32(data + i * 4, bigEndian);\n'
         '    }\n'
         '    // dusk stereo: an untagged load means "not J3D-classified" -- back to unknown/glued.\n'
         '    g_gxState.ptTexMtxClass[mtxIdx] = 0;\n'),
        ('      .instanceCount = instanceCount,\n'
         '      .bindGroups = bindGroups,\n'
         '      .dstAlpha = g_gxState.dstAlpha,\n'
         '  });\n',
         '      .instanceCount = instanceCount,\n'
         '      .bindGroups = bindGroups,\n'
         '      .dstAlpha = g_gxState.dstAlpha,\n'
         '      // NEAR-DOUBLING \xa75: the draw\'s transparency class (blend / additive / no-Z / alpha-test)\n'
         '      // -- render() flattens whichever class the Flat-mode picks, to find the sprite class.\n'
         '      .stereoClass = static_cast<uint8_t>(\n'
         '          (g_gxState.blendMode == GX_BM_BLEND ? 1u : 0u) |\n'
         '          (g_gxState.blendFacDst == GX_BL_ONE ? 2u : 0u) |\n'
         '          (!g_gxState.depthUpdate ? 4u : 0u) |\n'
         '          ((g_gxState.alphaCompare.comp0 != GX_ALWAYS || g_gxState.alphaCompare.comp1 != GX_ALWAYS) ? 8u\n'
         '                                                                                                     : 0u)),\n'
         '  });\n'),
    ],
    "lib/gfx/common.hpp": [
        ('extern wgpu::BindGroupLayout g_uniformBindGroupLayout;\n'
         'extern wgpu::BindGroup g_uniformBindGroup;\n',
         'extern wgpu::BindGroupLayout g_uniformBindGroupLayout;\n'
         'extern wgpu::BindGroup g_uniformBindGroup;\n'
         '// visionOS stereo 3D (STEREO-3D-RECOVERY-FABLE): a tiny per-execution uniform at\n'
         '// @group(3) holding the eye offset. Index 0=OFF(e=0)/1=LEFT/2=RIGHT; g_stereoEye is\n'
         '// set on the render worker only (begin/end frame) and read by gx::render.\n'
         'extern wgpu::BindGroupLayout g_stereoBindGroupLayout;\n'
         'extern std::array<wgpu::BindGroup, 3> g_stereoBindGroups;\n'
         'extern int g_stereoEye;\n'),
        ('using EndFrameCallback = std::function<void(wgpu::CommandEncoder&)>;\n',
         '// eye: 0 for the sole/left composite, 1 for the right (stereo second execution).\n'
         '// isLast: finalize (submit/present/pace) on this call -- true for the sole/last eye.\n'
         'using EndFrameCallback = std::function<void(wgpu::CommandEncoder&, int eye, bool isLast)>;\n'
         '\n'
         '// visionOS stereo 3D (STEREO-3D-RECOVERY-FABLE). When enabled, end_frame executes\n'
         '// the recorded frame packet TWICE (LEFT then RIGHT eye offset) and invokes the\n'
         '// callback once per eye. stereo_set_params updates the LEFT/RIGHT eye offset each\n'
         '// frame: halfSep = camera-space half eye-separation, invConv = 1/convergence dist.\n'
         'void stereo_set_enabled(bool on);\n'
         'void stereo_set_params(float halfSep, float invConv, float kFar, float kNear, float kGradC);\n'
         'void stereo_set_debug(bool on); // WATER-ANSWER \xa7D false-color probe (flags bit 0)\n'
         'void stereo_set_texamt(float amt); // WATER-ANSWER projected-texcoord shift strength (slot 4; 0=off)\n'),
    ],
    "lib/gfx/common.cpp": [
        # globals
        ('wgpu::BindGroupLayout g_uniformBindGroupLayout;\n'
         'wgpu::BindGroup g_uniformBindGroup;\n'
         '\n'
         '// for imgui debug\n',
         'wgpu::BindGroupLayout g_uniformBindGroupLayout;\n'
         'wgpu::BindGroup g_uniformBindGroup;\n'
         '\n'
         '// visionOS stereo 3D: @group(3) eye-offset uniform (0=OFF,1=LEFT,2=RIGHT).\n'
         'wgpu::BindGroupLayout g_stereoBindGroupLayout;\n'
         'std::array<wgpu::Buffer, 3> g_stereoBuffers;\n'
         'std::array<wgpu::BindGroup, 3> g_stereoBindGroups;\n'
         'int g_stereoEye = 0;            // render-worker-only; read by gx::render\n'
         'static std::atomic<bool> g_stereoEnabled{false};   // set on main, read on the worker\n'
         'static std::atomic<float> g_stereoHalfSep{0.f};    // set on main, applied on the worker\n'
         'static std::atomic<float> g_stereoInvConv{0.f};\n'
         'static std::atomic<float> g_stereoKFar{0.5f};      // far-depth knee (FOLLOWUP-ANSWER B)\n'
         'static std::atomic<bool> g_stereoDbg{false};       // WATER-ANSWER \xa7D false-color probe (flags bit 0)\n'
         'static std::atomic<float> g_stereoTexAmt{0.f};     // WATER-ANSWER fix: projected-texcoord shift strength (slot 4; 0=off)\n'
         'static std::atomic<float> g_stereoKNear{1.f};      // near-disparity knee (slot 5; V-doubling clamp, Near-depth slider)\n'
         'static std::atomic<float> g_stereoKGradC{0.f};     // softplus dz-floor s=kGrad*C (slot 6; slope-step smoothing)\n'
         '\n'
         '// for imgui debug\n'),
        # creation block
        ('    g_uniformBindGroup = g_device.CreateBindGroup(&bindGroupDescriptor);\n'
         '  }\n'
         '\n'
         '  gx::initialize();\n',
         '    g_uniformBindGroup = g_device.CreateBindGroup(&bindGroupDescriptor);\n'
         '  }\n'
         '  {\n'
         '    // visionOS stereo: @group(3) eye-offset uniform. Three buffers (OFF/LEFT/RIGHT),\n'
         '    // 32 bytes each (e, inv_conv, kfar, flags, txs, pad*3). All zero at init => e=0 =>\n'
         '    // bit-identical mono until stereo_set_params updates LEFT/RIGHT and _set_enabled turns on.\n'
         '    constexpr wgpu::BindGroupLayoutEntry layoutEntry{\n'
         '        .binding = 0,\n'
         '        // Fragment too: the WATER-ANSWER \xa7D false-color probe reads stereo.dbg in fs_main.\n'
         '        .visibility = wgpu::ShaderStage::Vertex | wgpu::ShaderStage::Fragment,\n'
         '        .buffer = wgpu::BufferBindingLayout{.type = wgpu::BufferBindingType::Uniform},\n'
         '    };\n'
         '    const wgpu::BindGroupLayoutDescriptor layoutDesc{\n'
         '        .label = "Stereo bind group layout",\n'
         '        .entryCount = 1,\n'
         '        .entries = &layoutEntry,\n'
         '    };\n'
         '    g_stereoBindGroupLayout = g_device.CreateBindGroupLayout(&layoutDesc);\n'
         '    for (size_t i = 0; i < g_stereoBuffers.size(); ++i) {\n'
         '      const wgpu::BufferDescriptor bufDesc{\n'
         '          .label = "Stereo uniform",\n'
         '          .usage = wgpu::BufferUsage::Uniform | wgpu::BufferUsage::CopyDst,\n'
         '          .size = 32,\n'
         '          .mappedAtCreation = true,\n'
         '      };\n'
         '      g_stereoBuffers[i] = g_device.CreateBuffer(&bufDesc);\n'
         '      auto* p = static_cast<float*>(g_stereoBuffers[i].GetMappedRange(0, 32));\n'
         '      for (int j = 0; j < 8; ++j) { p[j] = 0.f; }\n'
         '      g_stereoBuffers[i].Unmap();\n'
         '      const wgpu::BindGroupEntry entry{.binding = 0, .buffer = g_stereoBuffers[i], .size = 32};\n'
         '      const wgpu::BindGroupDescriptor bgDesc{\n'
         '          .label = "Stereo bind group",\n'
         '          .layout = g_stereoBindGroupLayout,\n'
         '          .entryCount = 1,\n'
         '          .entries = &entry,\n'
         '      };\n'
         '      g_stereoBindGroups[i] = g_device.CreateBindGroup(&bgDesc);\n'
         '    }\n'
         '  }\n'
         '\n'
         '  gx::initialize();\n'),
        # begin_frame: per-frame eye apply on the worker
        ('    webgpu::gpu_prof::frame_begin(g_framePackets[frameSlot].encoder);\n'
         '  });\n'
         '  g_cpuFrameStart = PresentClock::now();\n',
         '    webgpu::gpu_prof::frame_begin(g_framePackets[frameSlot].encoder);\n'
         '    // Stereo: the incremental encode of this frame\'s ops uses the LEFT eye (OFF when\n'
         '    // stereo is disabled). end_frame flips to RIGHT for the second execution.\n'
         '    g_stereoEye = g_stereoEnabled.load() ? 1 : 0;\n'
         '    {\n'
         '      // Apply the per-frame eye offset HERE (on the worker) -- writing the queue from\n'
         '      // the main thread races the worker\'s submit and crashes in EndBlit. Queue-\n'
         '      // ordered before this frame\'s commands, so the draws see the current values.\n'
         '      const float hs = g_stereoHalfSep.load();\n'
         '      const float ic = g_stereoInvConv.load();\n'
         '      // index 2 = kfar: the far-depth knee (shader tanh-compresses the far side to this\n'
         '      // fraction of the natural far asymptote). FOLLOWUP-ANSWER B.\n'
         '      const float kf = g_stereoKFar.load();\n'
         '      // slot 3 = flags bitmask (WATER-ANSWER): bit 0 = false-color probe. slot 4 = txs, the\n'
         '      // projected-texcoord shift strength (the slider; 0=off). Written into ALL three\n'
         '      // buffers (incl. OFF, whose e=0 keeps mono bit-identical) so the probe reads in 2D/sim.\n'
         '      const float md = (g_stereoDbg.load() ? 1.f : 0.f);\n'
         '      const float ta = g_stereoTexAmt.load();\n'
         '      const float kn = g_stereoKNear.load(); // slot 5 = near-disparity knee\n'
         '      const float kg = g_stereoKGradC.load(); // slot 6 = softplus dz-floor s = kGrad*C\n'
         '      const std::array<float, 8> off{0.f, ic, kf, md, ta, kn, kg, 0.f};\n'
         '      const std::array<float, 8> left{-hs, ic, kf, md, ta, kn, kg, 0.f};\n'
         '      const std::array<float, 8> right{hs, ic, kf, md, ta, kn, kg, 0.f};\n'
         '      g_queue.WriteBuffer(g_stereoBuffers[0], 0, off.data(), sizeof(off));\n'
         '      g_queue.WriteBuffer(g_stereoBuffers[1], 0, left.data(), sizeof(left));\n'
         '      g_queue.WriteBuffer(g_stereoBuffers[2], 0, right.data(), sizeof(right));\n'
         '    }\n'
         '  });\n'
         '  g_cpuFrameStart = PresentClock::now();\n'),
        # end_frame: drop the early packet reset
        ('    auto encoder = std::move(packet.encoder);\n'
         '    const auto stats = packet.stats;\n'
         '    packet = {};\n'
         '    g_stats.drawCallCount = stats.drawCallCount;\n',
         '    auto encoder = std::move(packet.encoder);\n'
         '    const auto stats = packet.stats;\n'
         '    g_stats.drawCallCount = stats.drawCallCount;\n'),
        # end_frame: the two-eye callback + packet re-execution
        ('    g_stats.lastTextureUploadSize = stats.lastTextureUploadSize;\n'
         '    if (callback) {\n'
         '      callback(encoder);\n'
         '    }\n'
         '    g_frameSlots.release(frameSlot);\n',
         '    g_stats.lastTextureUploadSize = stats.lastTextureUploadSize;\n'
         '    // The LEFT eye was rendered incrementally into `encoder` (g_stereoEye==1, or OFF).\n'
         '    // Composite it (eye 0). Then in stereo, RE-EXECUTE the whole recorded packet with\n'
         '    // the RIGHT eye offset and composite that (eye 1). packet.ops carries every pass in\n'
         '    // encode order, so this renders the complete frame a second time -- no fifo replay,\n'
         '    // no drain interception. Ordering in the command stream: LEFT frame -> eye0 resample\n'
         '    // -> RIGHT frame (overwrites the shared targets) -> eye1 resample.\n'
         '    const bool stereo = g_stereoEnabled.load();\n'
         '    if (callback) {\n'
         '      callback(encoder, 0, /*isLast=*/!stereo);\n'
         '    }\n'
         '    if (stereo) {\n'
         '      g_stereoEye = 2; // RIGHT\n'
         '      for (const auto& op : packet.ops) {\n'
         '        encode_op(encoder, packet, op);\n'
         '      }\n'
         '      g_stereoEye = 1; // restore LEFT for the next frame\'s incremental encode\n'
         '      if (callback) {\n'
         '        callback(encoder, 1, /*isLast=*/true);\n'
         '      }\n'
         '    }\n'
         '    packet = {};\n'
         '    g_frameSlots.release(frameSlot);\n'),
        # the stereo control functions
        ('uint32_t current_frame() noexcept { return g_frameIndex; }\n',
         'uint32_t current_frame() noexcept { return g_frameIndex; }\n'
         '\n'
         'void stereo_set_enabled(bool on) { g_stereoEnabled.store(on); }\n'
         '\n'
         'void stereo_set_params(float halfSep, float invConv, float kFar, float kNear, float kGradC) {\n'
         '  // Store only; the render worker writes the GPU buffers in begin_frame (writing the\n'
         '  // queue from this main thread would race the worker\'s submit).\n'
         '  g_stereoHalfSep.store(halfSep);\n'
         '  g_stereoInvConv.store(invConv);\n'
         '  g_stereoKFar.store(kFar > 0.f ? kFar : 0.5f);\n'
         '  g_stereoKNear.store(kNear > 0.f ? kNear : 1.0f);\n'
         '  g_stereoKGradC.store(kGradC >= 0.f ? kGradC : 0.f);\n'
         '}\n'
         '\n'
         'void stereo_set_debug(bool on) { g_stereoDbg.store(on); } // WATER-ANSWER \xa7D false-color probe\n'
         'void stereo_set_texamt(float amt) { g_stereoTexAmt.store(amt); } // WATER-ANSWER projected-texcoord shift strength\n'),
    ],
}

chunks = []
for rel, edits in EDITS.items():
    orig = (VENDOR / rel).read_text()
    text = orig
    for old, new in edits:
        assert text.count(old) == 1, f"{rel}: anchor {old[:56]!r} count {text.count(old)}"
        text = text.replace(old, new)
    with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
         tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
        fa.write(orig); fb.write(text); fa.flush(); fb.flush()
        r = subprocess.run(["diff", "-u", "--label", f"a/extern/aurora/{rel}",
                            "--label", f"b/extern/aurora/{rel}", fa.name, fb.name], capture_output=True)
    assert r.returncode == 1, f"{rel}: no diff"
    chunks.append(r.stdout.decode())

out = ROOT / "overlay/patches/0035-aurora-stereo-engine.patch"
out.write_text(__doc__ + "\n" + "".join(chunks))
print(f"wrote {out}")
