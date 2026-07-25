#!/usr/bin/env python3
"""Overlay patch 0037 (Phase 2 / stereo): J3D texgen-class tag emission.

TEXGEN-SPLIT-ANSWER §1. The 2x2 texgen-bit probe proved the water shine and the
terrain overlay are the SAME (src==POS && projective postMtx) class -- the bits
cannot split view-projected chains (water: keep the txs texcoord shift) from
world-projected ones (ground overlay, cloud shadows: must stay glued or they lift
off the ground / tear the step). But TP's material system still KNOWS the intent
at runtime: J3DTexMtxMode (Projmap 2/8 = world, ViewProjmap 3/9 = view) is read
at every matrix load site. These hooks emit that mode as a 1-word XF tag to
scratch 0x5F8, ADJACENT to the matrix load in the same byte stream (GD display
list or immediate fifo), so aurora's drain-time parser (patch 0035) gets exact
load->class ordering by construction -- a plain global would desync across the
materials queued between drains (~5 drains/frame).

Two files, all four load choke points, TARGET_PC-gated (established practice,
d_drawlist.cpp): J3DTevs.cpp (matblock GD path: loadTexMtx / loadPostTexMtx) and
J3DShapeMtx.cpp (per-shape differed path: the PT and TEXMTX GXLoadTexMtxImm sites,
which have the mode in scope as sp_4c / tex_gen_src).

Direct-GX draws (e.g. dDlst_shadowControl_c drop shadows) never tag -> class 0 ->
glued, which is the correct fail-safe default (TEXGEN-SPLIT-ANSWER "class policy").
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor/dusklight"

# rel -> list of (old, new) sequential string replacements
EDITS = {
    "libs/JSystem/src/J3DGraphBase/J3DTevs.cpp": [
        # matblock GD path: both J3DTexMtx load methods tag their slot with this->mode.
        # NOTE: the original loadTexMtx body line begins with a TAB -- preserved verbatim.
        ('void J3DTexMtx::loadTexMtx(u32 param_0) const {\n'
         '    GDOverflowCheck(0x35);\n'
         '\tJ3DGDLoadTexMtxImm((MtxP)mMtx, param_0 * 3 + 30, (GXTexMtxType)mTexMtxInfo.mProjection);\n'
         '}\n'
         '\n'
         'void J3DTexMtx::loadPostTexMtx(u32 param_0) const {\n'
         '    GDOverflowCheck(0x35);\n'
         '    J3DGDLoadPostTexMtxImm((MtxP)mMtx, param_0 * 3 + 0x40);\n'
         '}\n',
         'void J3DTexMtx::loadTexMtx(u32 param_0) const {\n'
         '#ifdef TARGET_PC\n'
         '    // dusk stereo (TEXGEN-SPLIT-ANSWER \xa71): tag the load with its J3DTexMtxMode so aurora\n'
         '    // can split view-projected chains (water shine -- takes the stereo "Texture depth"\n'
         '    // shift) from world-projected ones (terrain overlay, cloud shadows -- stay glued to\n'
         '    // the ground). The 9-byte tag rides the display list ADJACENT to the load; a plain\n'
         '    // global would desync across the materials queued between fifo drains.\n'
         '    GDOverflowCheck(0x35 + 9);\n'
         '\tJ3DGDLoadTexMtxImm((MtxP)mMtx, param_0 * 3 + 30, (GXTexMtxType)mTexMtxInfo.mProjection);\n'
         '    J3DGDWriteXFCmdHdr(0x5F8, 1);\n'
         '    J3DGDWrite_u32((u32)((param_0 * 3 + 30) << 8) | (u32)(mTexMtxInfo.mInfo & 0x3f));\n'
         '#else\n'
         '    GDOverflowCheck(0x35);\n'
         '\tJ3DGDLoadTexMtxImm((MtxP)mMtx, param_0 * 3 + 30, (GXTexMtxType)mTexMtxInfo.mProjection);\n'
         '#endif\n'
         '}\n'
         '\n'
         'void J3DTexMtx::loadPostTexMtx(u32 param_0) const {\n'
         '#ifdef TARGET_PC\n'
         '    // dusk stereo (TEXGEN-SPLIT-ANSWER \xa71): class tag rides adjacent -- see loadTexMtx.\n'
         '    GDOverflowCheck(0x35 + 9);\n'
         '    J3DGDLoadPostTexMtxImm((MtxP)mMtx, param_0 * 3 + 0x40);\n'
         '    J3DGDWriteXFCmdHdr(0x5F8, 1);\n'
         '    J3DGDWrite_u32((u32)((param_0 * 3 + 0x40) << 8) | (u32)(mTexMtxInfo.mInfo & 0x3f));\n'
         '#else\n'
         '    GDOverflowCheck(0x35);\n'
         '    J3DGDLoadPostTexMtxImm((MtxP)mMtx, param_0 * 3 + 0x40);\n'
         '#endif\n'
         '}\n'),
    ],
    "libs/JSystem/src/J3DGraphBase/J3DShapeMtx.cpp": [
        # the aurora-exported tag helper, declared in the file's existing TARGET_PC block
        ('#ifdef TARGET_PC\n'
         'static void J3DFrameInterpConcat(MtxP lhs, MtxP rhs, Mtx out) {\n',
         '#ifdef TARGET_PC\n'
         '// dusk stereo (TEXGEN-SPLIT-ANSWER \xa71): aurora-exported texgen-class tag -- a 1-word XF\n'
         '// write riding the GX fifo adjacent to each matrix load, so the drain-time parser sees\n'
         '// exact load->class ordering. Classes: J3DTexMtxMode (ViewProjmap 3/9 = view-projected).\n'
         'extern "C" void duskTagTexMtxClass(u32 gxMtxId, u32 cls);\n'
         '\n'
         'static void J3DFrameInterpConcat(MtxP lhs, MtxP rhs, Mtx out) {\n'),
        # differed path, PT branch (mode in scope as sp_4c)
        ('                GXLoadTexMtxImm(*mtx, i * 3 + GX_PTTEXMTX0, GX_MTX3x4);\n',
         '                GXLoadTexMtxImm(*mtx, i * 3 + GX_PTTEXMTX0, GX_MTX3x4);\n'
         '#ifdef TARGET_PC\n'
         '                duskTagTexMtxClass((u32)(i * 3 + GX_PTTEXMTX0), sp_4c); // dusk stereo class tag\n'
         '#endif\n'),
        # differed path, TEXMTX branch (mode in scope as tex_gen_src)
        ('                GXLoadTexMtxImm(*mtx, i * 3 + GX_TEXMTX0, (GXTexMtxType)tex_mtx_info_1->mProjection);\n',
         '                GXLoadTexMtxImm(*mtx, i * 3 + GX_TEXMTX0, (GXTexMtxType)tex_mtx_info_1->mProjection);\n'
         '#ifdef TARGET_PC\n'
         '                duskTagTexMtxClass((u32)(i * 3 + GX_TEXMTX0), tex_gen_src); // dusk stereo class tag\n'
         '#endif\n'),
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
        r = subprocess.run(["diff", "-u", "--label", f"a/{rel}",
                            "--label", f"b/{rel}", fa.name, fb.name], capture_output=True)
    assert r.returncode == 1, f"{rel}: no diff"
    chunks.append(r.stdout.decode())

out = ROOT / "overlay/patches/0037-dusk-j3d-texgen-class-tag.patch"
out.write_text(__doc__ + "\n" + "".join(chunks))
print(f"wrote {out}")
