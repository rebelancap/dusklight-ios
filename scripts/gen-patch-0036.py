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

Also exports gDusk3DInTransition (IRONJ item 2): non-zero while fopOvlpM has a
scene/overlay request in flight (wipe, fade, room/stage load). aurora ramps the
eye separation to zero across a transition -- there is no stable depth in one, so
stereo on it reads as a smear. Published from begin_frame, which m_Do_main drives
on every host frame (record_camera stops updating while the camera process is
suspended mid-load, which is exactly the window the flag must cover).

2026-10 sync: upstream split frame interpolation into src/dusk/interp/. The
camera export moved with record_camera to interp/camera.cpp (its gate is now
is_enabled() instead of g_enabled); the transition flag moved with begin_frame to
interp/frame_interpolation.cpp. begin_frame(float) now runs whenever the sim
advances (every host frame in lock-step mode, and before each batch of sim ticks
in the decoupled-presentation mode) -- fopOvlpM state only changes inside sim
ticks, so the flag stays exact. The definition anchors on sim_tick_seq(), the
first function above begin_frame in the namespace.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor/dusklight"

OUT = '0036-dusk-camdist-export.patch'

EDITS = [
    ("src/dusk/interp/camera.cpp", [
    # 1. includes for the player-actor + position accessors
    ('#include "f_op/f_op_camera_mng.h"\n'
     '#include "m_Do/m_Do_graphic.h"\n',
     '#include "f_op/f_op_camera_mng.h"\n'
     '#include "f_op/f_op_actor_mng.h"\n'
     '#include "m_Do/m_Do_graphic.h"\n'
     '#include "d/actor/d_a_player.h"\n'
     '\n'
     '#include <cmath>\n'),
    # 2. export eye->look-at AND eye->Link at the top of record_camera
    ('void record_camera(::camera_process_class* cam, int camera_id) {\n'
     '    if (!is_enabled() || camera_id != 0 || cam == nullptr) {\n',
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
     '    if (!is_enabled() || camera_id != 0 || cam == nullptr) {\n'),
    ]),
    ("src/dusk/interp/frame_interpolation.cpp", [
    # 3. IRONJ item 2: define the scene-transition flag. Non-zero while fopOvlpM has a
    #    scene/overlay request in flight (wipe, fade, room/stage load). Defined near the
    #    top of the namespace because begin_frame (edit 4) sits above record_camera.
    ('#include "mtx.h"\n',
     '#include "f_op/f_op_overlap_mng.h"\n'
     '#include "mtx.h"\n'),
    ('uint64_t sim_tick_seq() {\n',
     '// visionOS stereo 3D (IRONJ item 2): non-zero while fopOvlpM has a scene/overlay\n'
     '// request in flight -- i.e. a wipe/fade/room-or-stage load. aurora ramps the eye\n'
     '// separation to 0 across it (patch 0032); a transition has no stable depth, so\n'
     '// stereo on it reads as a smear.\n'
     'extern "C" volatile int gDusk3DInTransition = 0;\n'
     '\n'
     'uint64_t sim_tick_seq() {\n'),
    # 4. publish it every host frame. begin_frame is driven by m_Do_main on ALL three
    #    paths, unlike record_camera which stops updating while the camera process is
    #    suspended mid-load -- exactly the window we need the flag to be true in.
    ('void begin_frame(float step) {\n'
     '    const game_clock::FrameTiming& timing = game_clock::g_frameTiming;\n',
     'void begin_frame(float step) {\n'
     '    gDusk3DInTransition = (fopOvlpM_IsDoingReq() != 0) ? 1 : 0;\n'
     '    const game_clock::FrameTiming& timing = game_clock::g_frameTiming;\n'),
    ]),
]

chunks = []
for rel, edits in EDITS:
    src = VENDOR / rel
    orig = src.read_text()
    text = orig
    for e in edits:
        old, new = e[0], e[1]
        want = e[2] if len(e) > 2 else 1
        n = text.count(old)
        assert n == want, f"{rel}: expected {want} match(es), got {n} for:\n{old[:160]!r}"
        text = text.replace(old, new)
    with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
         tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
        fa.write(orig); fb.write(text); fa.flush(); fb.flush()
        r = subprocess.run(["diff", "-u", "--label", f"a/{rel}", "--label", f"b/{rel}",
                            fa.name, fb.name], capture_output=True)
    assert r.returncode == 1, f"{rel}: diff produced no change"
    chunks.append(r.stdout.decode())

out = ROOT / "overlay/patches/" / OUT
out.write_text(__doc__ + "\n" + "".join(chunks))
print(f"wrote {out} ({len(chunks)} file(s))")
