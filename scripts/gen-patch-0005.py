#!/usr/bin/env python3
"""Overlay patch 0005: give visionOS iOS's data-folder semantics (follows 0004).

Two more TARGET_OS_IOS gates in data.hpp that visionOS falls through, both of
which become *incoherent* once patch 0004 puts the visionOS data dir in
Documents like iOS:

1. DUSK_CAN_OPEN_DATA_FOLDER
       #if defined(_WIN32) || (defined(__APPLE__) && !TARGET_OS_IOS && !TARGET_OS_TV
           && !TARGET_OS_MACCATALYST) || (defined(__linux__) && !defined(__ANDROID__))
   visionOS satisfies every !TARGET_OS_* term, so it evaluates to 1 -- i.e. xrOS
   claims it can reveal the data folder in a file manager. The implementation
   (data.cpp open_data_path) does SDL_OpenURL("file://...") -- a Finder/Explorer
   reveal. visionOS has no Finder, so this is a **dead button** in Settings
   (settings.cpp:520/1342, also ImGuiMenuTools/ImGuiConsole). iOS correctly
   reports 0. A "pristine" port does not ship buttons that cannot work.

2. DUSK_CAN_CHANGE_DATA_FOLDER
       #if (defined(__APPLE__) && TARGET_OS_IOS && !TARGET_OS_MACCATALYST)
       #define DUSK_CAN_CHANGE_DATA_FOLDER 0
   iOS says 0 because its data folder is pinned to Documents. TARGET_OS_IOS is 0
   on visionOS, so xrOS said 1 -- offering to relocate a directory that patch
   0004 has just pinned to Documents. Same reasoning as iOS now applies, so it
   must report 0 too.

2026-10 sync: upstream moved the folder capabilities into extern/borealis
(borealis::data::Manager::capabilities()). DUSK_CAN_OPEN_DATA_FOLDER still exists
in data.hpp (edit 1, unchanged), and borealis now has its OWN copy of the same
gate for Environment::canOpenFolder (edit 2, new anchor, same fix).
DUSK_CAN_CHANGE_DATA_FOLDER is gone upstream; its replacement,
canChangeLocation = !isIOS, is fixed by patch 0004's isIOS gate.

Both are the same bug class as patches 0002/0003/0004; see DECISIONS D-009 for
the table. Desktop, iOS, tvOS and Android behavior are unchanged: the added
terms only ever evaluate differently when TARGET_OS_VISION is 1.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor/dusklight"

OUT = '0005-dusk-visionos-data-folder-semantics.patch'

EDITS = [
    ("src/dusk/data.hpp", [
        # 1. visionOS cannot reveal a folder in a file manager -> not a "desktop".
        (
            '#if defined(_WIN32) ||                                                                             \\\n'
            '    (defined(__APPLE__) && !TARGET_OS_IOS && !TARGET_OS_TV && !TARGET_OS_MACCATALYST) ||           \\\n'
            '    (defined(__linux__) && !defined(__ANDROID__))\n'
            '#define DUSK_CAN_OPEN_DATA_FOLDER 1\n',

            '#if defined(_WIN32) ||                                                                             \\\n'
            '    (defined(__APPLE__) && !TARGET_OS_IOS && !TARGET_OS_TV && !TARGET_OS_MACCATALYST &&            \\\n'
            '     !TARGET_OS_VISION) ||                                                                         \\\n'
            '    (defined(__linux__) && !defined(__ANDROID__))\n'
            '#define DUSK_CAN_OPEN_DATA_FOLDER 1\n',
            1,
        ),
    ]),
    ("extern/borealis/src/data.cpp", [
        # 2. Same gate, borealis's own copy (drives capabilities().canOpenFolder).
        (
            '#if defined(_WIN32) ||                                                                             \\\n'
            '    (defined(__APPLE__) && !TARGET_OS_IOS && !TARGET_OS_TV && !TARGET_OS_MACCATALYST) ||           \\\n'
            '    (defined(__linux__) && !defined(__ANDROID__))\n'
            '    environment.canOpenFolder = true;\n',

            '#if defined(_WIN32) ||                                                                             \\\n'
            '    (defined(__APPLE__) && !TARGET_OS_IOS && !TARGET_OS_TV && !TARGET_OS_MACCATALYST &&            \\\n'
            '     !TARGET_OS_VISION) ||                                                                         \\\n'
            '    (defined(__linux__) && !defined(__ANDROID__))\n'
            '    environment.canOpenFolder = true;\n',
            1,
        ),
    ]),
]

chunks = []
for rel, edits in EDITS:
    src = VENDOR / rel
    orig = src.read_text()
    text = orig
    for old, new, want in edits:
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
