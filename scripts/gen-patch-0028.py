#!/usr/bin/env python3
"""Overlay patch 0028: persist the selected disc across relaunches (iOS/visionOS).

SYMPTOM (after a play session that crashed + auto-relaunched): "it asks
me to select the disc yet again... it asks for this way too often. why can't it
persist?"

ROOT CAUSE: the disc picker (`src/dusk/ios/FileSelectDialog.m`) presents the
UIDocumentPicker with `asCopy:YES`, so iOS hands back a COPY of the picked disc
inside the app's Inbox -- `<container>/tmp/<bundle-id>-Inbox/<name>.iso`. That
Inbox path is what gets stored in `backend.isoPath` and re-read at boot
(`m_Do_main.cpp:715`). iOS PURGES the Inbox between launches, so on the next
launch the stored path no longer exists -> `iso::validate` fails -> m_Do_main
logs "Saved DVD image path failed validation, clearing configured path" and
drops back to the prelaunch disc picker. Every relaunch (and every crash +
auto-relaunch) re-prompts, because the persisted path was transient all along.

FIX: when a disc is accepted (`apply_valid_disc_result`, the single funnel for
both the normal verified-success path and the "proceed at your own risk"
hash-mismatch path), relocate the Inbox copy into the persistent data dir
(Documents, where patch 0004 points `configured_data_path()`), and store THAT
stable path. Inbox and Documents live on the same app-container volume, so the
relocation is a rename -- instant regardless of the ~1.4 GB disc size -- with a
cross-volume copy+unlink fallback. The whole downstream flow (prelaunch state,
`activeDiscPath`, `backend.isoPath`, and thus the nod runtime read) then uses the
persistent path uniformly, so the choice survives relaunches and crashes.

Gated `(TARGET_OS_IOS || TARGET_OS_VISION)` -- the exact idiom the tree already
uses for the picker (`file_select.cpp:27`) and the iOS data dir
(`data.cpp:126`). macOS uses NSOpenPanel (real path, no Inbox), so it needs no
relocation and the helper is a straight pass-through there.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "src/dusk/ui/prelaunch.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()
text = orig

# --- hunk 1: TargetConditionals include (for the platform gate) --------------
inc_old = ('#include "m_Do/m_Do_MemCard.h"\n'
           '\n'
           'namespace dusk::ui {\n')
inc_new = ('#include "m_Do/m_Do_MemCard.h"\n'
           '\n'
           '#ifdef __APPLE__\n'
           '#include <TargetConditionals.h>\n'
           '#endif\n'
           '\n'
           'namespace dusk::ui {\n')
assert text.count(inc_old) == 1, f"include anchor: {text.count(inc_old)}"
text = text.replace(inc_old, inc_new)

# --- hunk 2: relocate helper + thread the persistent path through ------------
apply_old = ('void apply_valid_disc_result(\n'
             '    const std::string& path, const iso::DiscInfo& info, iso::ValidationError validation) {\n'
             '    auto& state = prelaunch_state();\n'
             '    state.configuredDiscPath = path;\n'
             '    state.configuredDiscCanLaunch = true;\n'
             '    state.configuredDiscInfo = info;\n'
             '    state.configuredDiscValidation = validation;\n'
             '    if (state.activeDiscPath.empty() || path == state.activeDiscPath) {\n'
             '        state.activeDiscPath = path;\n'
             '        state.activeDiscInfo = info;\n'
             '    }\n'
             '    persist_disc_choice(path, validation);\n'
             '}\n')
apply_new = (
    '// iOS/visionOS: the document picker returns the picked disc as a COPY in the\n'
    '// app\'s Inbox (<container>/tmp/<bundle-id>-Inbox/), which the OS purges between\n'
    '// launches -- so a stored Inbox path fails validation on the next launch and\n'
    '// Dusklight re-prompts for the disc every time (even after a crash + auto-\n'
    '// relaunch). Relocate the copy into the persistent data dir (Documents) and\n'
    '// return that stable path so the choice survives. Inbox and Documents share the\n'
    '// app-container volume, so the rename is instant regardless of the ~1.4 GB disc\n'
    '// size; a cross-volume fallback copies then unlinks the transient source.\n'
    '// Returns `path` unchanged on any failure or when it is already persistent\n'
    '// (non-Apple-mobile builds are a straight pass-through).\n'
    'std::string relocate_disc_to_persistent(const std::string& path) {\n'
    '#if defined(__APPLE__) && (TARGET_OS_IOS || TARGET_OS_VISION)\n'
    '    std::error_code ec;\n'
    '    const std::filesystem::path src{path};\n'
    '    const std::filesystem::path dataDir = dusk::data::configured_data_path();\n'
    '    if (dataDir.empty()) {\n'
    '        return path;\n'
    '    }\n'
    '    const std::filesystem::path dest = dataDir / src.filename();\n'
    '    if (src == dest || src.parent_path() == dataDir) {\n'
    '        return path;  // already inside persistent storage\n'
    '    }\n'
    '    std::filesystem::create_directories(dataDir, ec);\n'
    '    ec.clear();\n'
    '    std::filesystem::remove(dest, ec);  // replace any previously-stored disc\n'
    '    ec.clear();\n'
    '    std::filesystem::rename(src, dest, ec);\n'
    '    if (ec) {\n'
    '        // Different volume (or rename unsupported): copy, then drop the source.\n'
    '        ec.clear();\n'
    '        std::filesystem::copy_file(\n'
    '            src, dest, std::filesystem::copy_options::overwrite_existing, ec);\n'
    '        if (!ec) {\n'
    '            std::error_code rmEc;\n'
    '            std::filesystem::remove(src, rmEc);\n'
    '        }\n'
    '    }\n'
    '    if (ec) {\n'
    '        PrelaunchLog.warn(\n'
    '            "Could not relocate disc into persistent storage ({}); keeping picked path: {}",\n'
    '            ec.message(), path);\n'
    '        return path;\n'
    '    }\n'
    '    PrelaunchLog.info("Relocated selected disc into persistent storage: {}", dest.string());\n'
    '    return dest.string();\n'
    '#else\n'
    '    return path;\n'
    '#endif\n'
    '}\n'
    '\n'
    'void apply_valid_disc_result(\n'
    '    const std::string& path, const iso::DiscInfo& info, iso::ValidationError validation) {\n'
    '    const std::string effectivePath = relocate_disc_to_persistent(path);\n'
    '    auto& state = prelaunch_state();\n'
    '    state.configuredDiscPath = effectivePath;\n'
    '    state.configuredDiscCanLaunch = true;\n'
    '    state.configuredDiscInfo = info;\n'
    '    state.configuredDiscValidation = validation;\n'
    '    if (state.activeDiscPath.empty() || effectivePath == state.activeDiscPath) {\n'
    '        state.activeDiscPath = effectivePath;\n'
    '        state.activeDiscInfo = info;\n'
    '    }\n'
    '    persist_disc_choice(effectivePath, validation);\n'
    '}\n')
assert text.count(apply_old) == 1, f"apply anchor: {text.count(apply_old)}"
text = text.replace(apply_old, apply_new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0028-dusk-visionos-persist-disc-choice.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
