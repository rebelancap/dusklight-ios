#!/usr/bin/env python3
"""Overlay patch 0036 (Phase 2 / stereo): export TP's camera distance for convergence.

STEREO-3D-RECOVERY-FABLE §2/§4.1 + NEAR-DOUBLING-ANSWER-FABLE. The stereo
convergence plane must track the camera->SUBJECT distance -- and the subject is
Link, not the camera's look-at center. TP's field camera looks at the horizon
(~1810 units), so eye->look-at puts the zero plane miles out and the entire near
field (ground, flowers, butterflies) is crossed-biased -> unfixable near doubling
(the near knee can't referee the whole ground plane vs the extreme-near tail).
Anchoring on Link puts the subject on the panel; the far knee keeps the far look.

record_camera (d_camera.cpp) already hands dusk the main camera each frame. We
export BOTH distances: eye->center (gDusk3DCamDist, kept for logging/fallback) and
eye->Link (gDusk3DCamDistLink, the one aurora converges on -- patch 0032). Link's
position comes from daPy_getPlayerActorClass()/fopAcM_GetPosition_p (the canonical
accessors; see d_a_formation_mng.h:191). The compute runs UN-gated by
frame-interpolation (before the g_enabled early-out) so it is available whenever
the camera updates.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "src/dusk/frame_interpolation.cpp"
SRC = ROOT / "vendor/dusklight" / REL

EDITS = [
    # 1. includes for the player-actor + position accessors
    ('#include "f_op/f_op_camera_mng.h"\n'
     '#include "m_Do/m_Do_graphic.h"\n'
     '#include "mtx.h"\n',
     '#include "f_op/f_op_camera_mng.h"\n'
     '#include "f_op/f_op_actor_mng.h"\n'
     '#include "m_Do/m_Do_graphic.h"\n'
     '#include "d/actor/d_a_player.h"\n'
     '#include "mtx.h"\n'),
    # 2. export eye->look-at AND eye->Link at the top of record_camera
    ('void record_camera(::camera_process_class* cam, int camera_id) {\n'
     '    if (!g_enabled || camera_id != 0 || cam == nullptr) {\n',
     '// visionOS stereo 3D: export the main camera\'s eye->look-at distance so aurora can\n'
     '// set the convergence plane (STEREO-3D-RECOVERY-FABLE \xa74.1). 0 => not yet known.\n'
     'extern "C" volatile float gDusk3DCamDist = 0.0f;\n'
     '// Subject-anchored convergence (NEAR-DOUBLING-ANSWER-FABLE): eye->Link distance. TP\'s field\n'
     '// camera looks at the HORIZON, so eye->look-at puts the zero plane miles out and the whole\n'
     '// near field (ground, flowers, butterflies) is crossed-biased -> unfixable near doubling.\n'
     '// The zero plane belongs on the subject (Link); aurora prefers this, falling back to a\n'
     '// capped look-at for player-less frames (cutscenes/menus). 0 => Link not known this frame.\n'
     'extern "C" volatile float gDusk3DCamDistLink = 0.0f; // = eye->getPlayer(0)->current.pos (candidate P0cur)\n'
     '// NEAR-DOUBLING readout probe (STEP-AND-SHADOWS-ANSWER \xa7A): the P0cur candidate above came back\n'
     '// garbage, so log ALL FOUR candidates and pick the ONE correct camera-eye->Link expression by\n'
     '// data. p0 = getPlayer(0), pL = getLinkPlayer (LINK_PTR slot); current.pos (feet) vs eyePos (head).\n'
     'extern "C" volatile float gDusk3DLinkP0Eye = 0.0f;\n'
     'extern "C" volatile float gDusk3DLinkPLCur = 0.0f;\n'
     'extern "C" volatile float gDusk3DLinkPLEye = 0.0f;\n'
     '\n'
     'void record_camera(::camera_process_class* cam, int camera_id) {\n'
     '    if (camera_id == 0 && cam != nullptr) {\n'
     '        const auto& e = cam->view.lookat.eye;\n'
     '        const auto& c = cam->view.lookat.center;\n'
     '        const float dx = e.x - c.x, dy = e.y - c.y, dz = e.z - c.z;\n'
     '        gDusk3DCamDist = std::sqrt(dx * dx + dy * dy + dz * dz);\n'
     '        auto distFrom = [&](const cXyz& q) {\n'
     '            const float lx = e.x - q.x, ly = e.y - q.y, lz = e.z - q.z;\n'
     '            return std::sqrt(lx * lx + ly * ly + lz * lz);\n'
     '        };\n'
     '        daPy_py_c* p0 = daPy_getPlayerActorClass();\n'
     '        daPy_py_c* pL = daPy_getLinkPlayerActorClass();\n'
     '        gDusk3DCamDistLink = (p0 != nullptr) ? distFrom(p0->current.pos) : 0.0f;\n'
     '        gDusk3DLinkP0Eye = (p0 != nullptr) ? distFrom(p0->eyePos) : 0.0f;\n'
     '        gDusk3DLinkPLCur = (pL != nullptr) ? distFrom(pL->current.pos) : 0.0f;\n'
     '        gDusk3DLinkPLEye = (pL != nullptr) ? distFrom(pL->eyePos) : 0.0f;\n'
     '    }\n'
     '    if (!g_enabled || camera_id != 0 || cam == nullptr) {\n'),
]

orig = SRC.read_text()
text = orig
for old, new in EDITS:
    assert text.count(old) == 1, f"anchor count {text.count(old)} for {old[:48]!r}"
    text = text.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0036-dusk-camdist-export.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
