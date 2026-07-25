#!/usr/bin/env python3
"""Overlay patch 0012: seed the data dir so the Files folder is visible.

THE bug behind "my Dusklight folder is hidden, I can't move the ROM in": on a
fresh install the app *reads* from Documents (config, texture_replacements) but
writes NOTHING there -- config saves only on change, and the texture_replacements
dir is scanned but never created. An app with an EMPTY Documents dir is HIDDEN in
the Files app (VISION-PRO-GUIDE 1.5), so the user can never open it to drop their
disc dump. Chicken-and-egg: empty -> hidden -> can't add data -> stays empty.

Verified in the sim: fresh launch, app healthy, `Documents/` completely empty;
the log reads config from `Documents/config.json` ("did not exist") and scans
`Documents/texture_replacements` ("Loaded 0") without creating either.

(DECISIONS D-009 claimed no seed was needed because "the app populates Documents
at first launch." That was WRONG -- it populates the *pref* path, not Documents.
This patch is the correction.)

Fix: in initialize_data(), right after the data dir is ensured, create the
texture_replacements/ subdir and write a README.txt (only if absent) explaining
where the disc dump and texture packs go. Non-empty Documents -> the folder shows
in Files -> the user can drop their ROM.

Unconditional (all platforms): creating texture_replacements/ is harmless (it is
scanned anyway) and a readme in the data dir is helpful everywhere. io::FileStream
and <filesystem> are already included in data.cpp.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "src/dusk/data.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()

old = ('    migrate_data(prefPath, dataPath, descriptor ? &descriptor->descriptor : nullptr);\n'
       '    ensure_data_directory(dataPath);\n'
       '    ensure_data_directory(prefPath);\n')
new = ('    migrate_data(prefPath, dataPath, descriptor ? &descriptor->descriptor : nullptr);\n'
       '    ensure_data_directory(dataPath);\n'
       '    ensure_data_directory(prefPath);\n'
       '\n'
       '    // Seed the data dir so it is non-empty: an app with an empty Documents dir\n'
       '    // is HIDDEN in the Files app, leaving nowhere to drop the disc dump. Create\n'
       '    // the texture_replacements folder and a one-time readme. Best-effort: this\n'
       '    // runs at startup, so a seed failure must never throw (WriteAllText can).\n'
       '    try {\n'
       '        std::error_code seedEc;\n'
       '        std::filesystem::create_directories(dataPath / "texture_replacements", seedEc);\n'
       '        const auto readmePath = dataPath / "README.txt";\n'
       '        if (!std::filesystem::exists(readmePath, seedEc)) {\n'
       '            io::FileStream::WriteAllText(\n'
       '                readmePath,\n'
       '                "Dusklight - your game data goes here\\n"\n'
       '                "\\n"\n'
       '                "Put your own GameCube Twilight Princess disc dump (USA or EUR .iso)\\n"\n'
       '                "in THIS folder, then launch Dusklight and choose it under\\n"\n'
       '                "\\"Select Disc Image\\".\\n"\n'
       '                "\\n"\n'
       '                "HD / 4K texture packs go in the \\"texture_replacements\\" folder here.\\n"\n'
       '                "\\n"\n'
       '                "This app ships with no game data. Nothing here is uploaded anywhere.\\n");\n'
       '        }\n'
       '    } catch (const std::exception& e) {\n'
       '        Log.warn("Failed to seed data directory: {}", e.what());\n'
       '    }\n')

n = orig.count(old)
assert n == 1, f"expected 1 match, got {n}"
text = orig.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0012-dusk-seed-documents-folder.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
