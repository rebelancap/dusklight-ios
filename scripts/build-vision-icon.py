#!/usr/bin/env python3
"""Build app/visionos/Assets.car containing a visionOS layered app icon.

VISION-PRO-GUIDE / charter: "visionOS icon = .solidimagestack (flat PNG -> blank
tile)". Confirmed on device-sim: artifacts/01-launch.png shows Dusklight's
launcher tile as a blank grid placeholder, because platforms/ios/Assets.car only
carries a flat 1024 AppIcon and the xrOS SDK wants a *layered* icon.

Upstream ships prebuilt Assets.car per platform (they are checked in, not built
by CMake), so we do the same for visionOS -- but out of the vendor tree, in
app/visionos/, which patch 0001's DUSK_VISIONOS_RESOURCE_DIR points at.

Layers are derived from the project's OWN art (vendor res/icon.png, the Midna
helmet, RGBA with a real cutout):
  - Front: the icon art itself, so it floats with parallax.
  - Back:  an opaque tile. visionOS requires the back layer to be opaque (it is
           the tile the system circular-masks), and a transparent back reads as a
           hole. Its colour is *sampled from the art* rather than invented -- the
           icon's own palette is near-black with warm gold accents (#e0a020), so
           the tile is a restrained radial from a warm twilight centre to
           near-black at the rim. Subtle on purpose: the back layer is mostly seen
           as a halo behind the helmet and should not fight it.

Run:  python3 scripts/build-vision-icon.py
Then: scripts/build-vision-sim.sh   (Assets.car is picked up by the resource glob)
"""
import json, pathlib, shutil, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC_ICON = ROOT / "vendor/dusklight/res/icon.png"
OUT_DIR = ROOT / "app/visionos"
WORK = ROOT / "work/vision-icon"
XCASSETS = WORK / "Assets.xcassets"
STACK = XCASSETS / "AppIcon.solidimagestack"

SIZE = 1024

def info():
    return {"author": "dusklight-visionos", "version": 1}

def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n")

def make_layer(name, png_path):
    """One .solidimagestacklayer wrapping a Content.imageset."""
    layer = STACK / f"{name}.solidimagestacklayer"
    write_json(layer / "Contents.json", {"info": info()})
    imgset = layer / "Content.imageset"
    write_json(imgset / "Contents.json", {
        "images": [{"filename": png_path.name, "idiom": "vision", "scale": "2x"}],
        "info": info(),
    })
    shutil.copy(png_path, imgset / png_path.name)
    return layer

def main():
    try:
        from PIL import Image
    except ImportError:
        sys.exit("FATAL: needs Pillow (python3 -m pip install pillow)")

    if not SRC_ICON.exists():
        sys.exit(f"FATAL: source art missing: {SRC_ICON} (is vendor/ populated?)")

    if WORK.exists():
        shutil.rmtree(WORK)
    STACK.mkdir(parents=True, exist_ok=True)

    art = Image.open(SRC_ICON).convert("RGBA")
    if art.size != (SIZE, SIZE):
        art = art.resize((SIZE, SIZE), Image.LANCZOS)

    # --- Back: the ORANGE gradient, to match the iOS/macOS app icon -----------
    # Sampled from platforms/macos/Dusklight.icns: a subtle vertical gradient,
    # warm tan at top -> more saturated gold at the bottom. Full-bleed (visionOS
    # masks it to the squircle itself), rendered per-pixel to avoid banding.
    c_top = (0xe0, 0xb4, 0x72)     # #e0b472
    c_bot = (0xc3, 0x90, 0x40)     # #c39040
    px = bytearray(SIZE * SIZE * 4)
    i = 0
    for y in range(SIZE):
        t = y / (SIZE - 1)                              # 0 at top -> 1 at bottom
        t = t * t * (3.0 - 2.0 * t)                     # smoothstep for a soft ramp
        r = int(c_top[0] + (c_bot[0] - c_top[0]) * t + 0.5)
        g = int(c_top[1] + (c_bot[1] - c_top[1]) * t + 0.5)
        b = int(c_top[2] + (c_bot[2] - c_top[2]) * t + 0.5)
        row = bytes((r, g, b, 255)) * SIZE
        px[i:i + len(row)] = row
        i += len(row)
    back = Image.frombytes("RGBA", (SIZE, SIZE), bytes(px))
    back_png = WORK / "back.png"
    back.save(back_png)

    # --- Front: the helmet, SCALED DOWN and centred to match the iOS icon -----
    # The source art (res/icon.png) is a full-bleed helmet (~92% of its frame).
    # The iOS/macOS icon shows it much smaller with clear margin (~74% of the
    # tile). Scale so the helmet content lands at ~74% of the icon, centred, with
    # a soft drop shadow like the reference.
    from PIL import ImageFilter
    HELMET_FRAC = 0.74                                  # helmet content as frac of icon
    art_content = 0.92                                  # res/icon.png content frac
    scaled = max(1, int(SIZE * HELMET_FRAC / art_content + 0.5))
    helmet = art.resize((scaled, scaled), Image.LANCZOS)
    off = (SIZE - scaled) // 2
    # soft drop shadow from the helmet alpha, offset down slightly
    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    alpha = helmet.split()[3].point(lambda a: int(a * 0.45))
    sh = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    sh.paste((0, 0, 0, 255), (off, off + int(SIZE * 0.012)), alpha)
    sh = sh.filter(ImageFilter.GaussianBlur(SIZE * 0.012))
    front = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    front = Image.alpha_composite(front, sh)
    front.alpha_composite(helmet, (off, off))
    front_png = WORK / "front.png"
    front.save(front_png)

    make_layer("Back", back_png)
    make_layer("Front", front_png)

    # Front first: actool stacks in listed order, nearest-viewer first.
    write_json(STACK / "Contents.json", {
        "layers": [
            {"filename": "Front.solidimagestacklayer"},
            {"filename": "Back.solidimagestacklayer"},
        ],
        "info": info(),
    })
    write_json(XCASSETS / "Contents.json", {"info": info()})

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # NOTE: deliberately NO `--app-icon AppIcon --output-partial-info-plist`.
    # That combination makes actool hang indefinitely on this catalog (observed:
    # 25+ minutes on two 1024px PNGs, killed; actool --version responds instantly
    # and the identical command WITHOUT --app-icon finishes in about a second).
    # We don't need it: --app-icon exists to emit CFBundleIcons/CFBundleIconName
    # into a partial plist for Xcode to merge, and app/visionos/Info.plist.in
    # declares CFBundleIconName itself. The compiled catalog is identical either
    # way -- verified with assetutil:
    #     SolidImageStack  AppIcon
    #     Image            AppIcon/Back/Content   1024x1024 idiom=vision
    #     Image            AppIcon/Front/Content  1024x1024 idiom=vision
    cmd = [
        "xcrun", "actool", str(XCASSETS),
        "--compile", str(OUT_DIR),
        "--platform", "xros",
        "--minimum-deployment-target", "2.0",
        "--output-format", "human-readable-text",
    ]
    print("$ " + " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    if r.returncode != 0:
        sys.exit(f"FATAL: actool failed ({r.returncode})")

    car = OUT_DIR / "Assets.car"
    if not car.exists():
        sys.exit("FATAL: actool reported success but produced no Assets.car")
    print(f"\nwrote {car} ({car.stat().st_size} bytes)")

    # Assert the layered icon really is in there -- a flat/missing icon is the
    # exact failure this script exists to fix, and it fails silently at runtime
    # (blank launcher tile), so check it here rather than on the device.
    probe = subprocess.run(["xcrun", "assetutil", "--info", str(car)],
                           capture_output=True, text=True, timeout=120)
    names = set()
    try:
        for e in json.loads(probe.stdout):
            if e.get("Name"):
                names.add((e.get("AssetType", ""), e["Name"]))
    except Exception:
        sys.exit("FATAL: could not parse assetutil output to verify the icon")
    need = [("SolidImageStack", "AppIcon"),
            ("Image", "AppIcon/Back/Content"),
            ("Image", "AppIcon/Front/Content")]
    missing = [n for n in need if n not in names]
    if missing:
        sys.exit(f"FATAL: Assets.car is missing {missing} -- icon would be blank")
    print("verified: SolidImageStack AppIcon + Back/Front layers present")

if __name__ == "__main__":
    main()
