#!/usr/bin/env python3
"""Overlay patch 0004: put visionOS's data dir in Documents, like iOS.

data.cpp picks the app's default data directory:

    std::filesystem::path default_data_path(const std::filesystem::path& prefPath) {
    #ifdef __APPLE__
    #if TARGET_OS_IOS && !TARGET_OS_TV
        ... return SDL_GetUserFolder(SDL_FOLDER_DOCUMENTS);   // Files-visible
    #endif
    #endif
        return prefPath;                                       // Library/Application Support
    }

TARGET_OS_IOS is 0 on visionOS (only TARGET_OS_IPHONE / TARGET_OS_VISION are 1),
so xrOS silently took the desktop branch. Measured on the running app:

    Loading config from '.../Library/Application Support/TwilitRealm/Dusklight/config.json'
    ... texture replacement registrations from '.../Library/Application Support/...'

That is the third instance of this same TARGET_OS_IOS-vs-visionOS bug class in
this tree (cf. patch 0002 aurora device, patch 0003 file_select).

Why it matters more than it looks:
  - Library/Application Support is NOT reachable from Files.app, so the user has
    nowhere to put their disc dump (QUESTIONS Q-002) and nowhere to install a
    Dolphin texture pack (Q-003) -- the two things the port exists to consume.
  - It also leaves Documents EMPTY, and an app with an empty Documents dir is
    hidden in Files entirely (VISION-PRO-GUIDE 1.5). Seeding a readme would have
    treated the symptom; the app was simply looking in the wrong place.

Fix: take the same Documents path iOS takes. Info.plist already ships
UIFileSharingEnabled + LSSupportsOpeningDocumentsInPlace, so Documents becomes
browsable and droppable, and config.json landing there at first launch is enough
to make the app appear in Files on its own.

TARGET_OS_VISION needs no #ifndef guard: TargetConditionals.h is reached via the
enclosing #ifdef __APPLE__, and an undefined identifier evaluates to 0 inside
#if, so older SDKs and every other platform keep their current behavior. iOS is
untouched.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "src/dusk/data.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()

old = ('#ifdef __APPLE__\n'
       '#if TARGET_OS_IOS && !TARGET_OS_TV\n'
       '    const char* documentsPath = SDL_GetUserFolder(SDL_FOLDER_DOCUMENTS);\n')
new = ('#ifdef __APPLE__\n'
       '#if (TARGET_OS_IOS || TARGET_OS_VISION) && !TARGET_OS_TV\n'
       '    const char* documentsPath = SDL_GetUserFolder(SDL_FOLDER_DOCUMENTS);\n')

n = orig.count(old)
assert n == 1, f"expected 1 match, got {n}"
text = orig.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0004-dusk-visionos-data-path-documents.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
