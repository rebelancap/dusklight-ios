#!/usr/bin/env python3
"""Overlay patch 0010: don't let libjpeg-turbo's ExternalProject inherit Xcode.

Second (and last) blocker to archiving/signing for OTA. With -GXcode the
libjpeg-turbo ExternalProject inherits the Xcode generator, and the archive dies:

    CMake Error at cmake_install.cmake:99 (file):
      file INSTALL cannot find
      ".../libjpeg-turbo-ext-build/RelWithDebInfo/tjbench-static":
      No such file or directory.

Cause: Xcode appends EFFECTIVE_PLATFORM_NAME to its config dir, so the sub-build
writes `RelWithDebInfo-xros/`, while libjpeg-turbo's own install step looks in
plain `RelWithDebInfo/`. (Note the missing file is `tjbench` -- a *benchmark*
tool we never use; it fails purely on the path mismatch.)

Fix: pin the sub-build to a single-config generator, which has no such suffix.
libjpeg-turbo is a leaf dependency built for its static lib -- nothing about it
needs to be in the Xcode project.

CMAKE_MAKE_PROGRAM must be overridden too, and this is the subtle part. It is in
`_jpeg_passthrough_vars`. I first assumed it was unset under Xcode because it is
absent from CMakeCache.txt -- **wrong**: the Xcode generator sets it as a *normal*
variable (`/usr/bin/xcodebuild`), and `if (DEFINED ...)` sees normal variables
too. So it got passed to the Ninja sub-build, which then probed it and died:

    CMake Error at CMakeLists.txt:7 (project):
      Running '/usr/bin/xcodebuild' '--version' failed with:
      xcodebuild: error: invalid option '--version'

Hence the explicit `-DCMAKE_MAKE_PROGRAM=<ninja>` appended *after* the passthrough
loop, where last-wins on the command line.

Generator-guarded, so Ninja/Makefiles builds -- including our own device build
that the measurements came from -- are byte-for-byte unchanged. Falls back
silently if ninja is absent, leaving the previous (broken-under-Xcode-only)
behavior rather than failing a build that would otherwise work.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "CMakeLists.txt"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()

old = ('        ExternalProject_Add(libjpeg-turbo-ext\n'
       '            URL https://github.com/libjpeg-turbo/libjpeg-turbo/archive/refs/tags/3.1.0.tar.gz\n')

new = ('        # The Xcode generator suffixes its config dir with\n'
       '        # EFFECTIVE_PLATFORM_NAME (RelWithDebInfo-xros), but libjpeg-turbo\'s\n'
       '        # install step looks in plain RelWithDebInfo/ -- so an inherited Xcode\n'
       '        # generator breaks `xcodebuild archive` on a missing tjbench-static.\n'
       '        # Pin the sub-build to a single-config generator; it is a leaf\n'
       '        # dependency and has no reason to live in the Xcode project.\n'
       '        set(_jpeg_ext_generator)\n'
       '        if (CMAKE_GENERATOR STREQUAL "Xcode")\n'
       '            find_program(_jpeg_ninja ninja)\n'
       '            if (_jpeg_ninja)\n'
       '                set(_jpeg_ext_generator CMAKE_GENERATOR "Ninja")\n'
       '                # The Xcode generator defines CMAKE_MAKE_PROGRAM as a *normal*\n'
       '                # variable (/usr/bin/xcodebuild), so the passthrough loop above\n'
       '                # forwards it and the Ninja sub-build then probes it with\n'
       '                # `xcodebuild --version` and dies. Appending here wins: last -D\n'
       '                # on the command line takes precedence.\n'
       '                list(APPEND _jpeg_cmake_args -DCMAKE_MAKE_PROGRAM=${_jpeg_ninja})\n'
       '            endif ()\n'
       '        endif ()\n'
       '        ExternalProject_Add(libjpeg-turbo-ext\n'
       '            ${_jpeg_ext_generator}\n'
       '            URL https://github.com/libjpeg-turbo/libjpeg-turbo/archive/refs/tags/3.1.0.tar.gz\n')

n = orig.count(old)
assert n == 1, f"expected 1 match, got {n}"
text = orig.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0010-dusk-libjpeg-ext-generator.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
