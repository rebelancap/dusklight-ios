# Installing Dusklight on Apple Vision Pro

This is an Apple Vision Pro build of [Dusklight](https://github.com/TwilitRealm/dusklight), TwilitRealm's from-scratch, CC0 reimplementation of The Legend of Zelda: Twilight Princess, with its [aurora](https://github.com/encounter/aurora) GX runtime rendering through Google's Dawn (WebGPU) onto Metal. It plays the GameCube game in a freely resizable 2D window, or in a stereoscopic 3D mode on a world-locked screen in your room.

## What you need

- Apple Vision Pro on visionOS 2 or later
- Your own GameCube Twilight Princess disc image
- For the prebuilt app: SideStore on the headset, installed with the [iloader fork for visionOS](https://github.com/rebelancap/iloader/releases#release-visionos) on an Apple Silicon Mac
- To build from source: macOS with Xcode (visionOS SDK), `cmake`, `ninja`, and a Rust **nightly** toolchain
- Optional: a game controller

## Your game files

Neither this repository nor the app contains any game content. You supply your own disc image.

1. You need a GameCube Twilight Princess dump: US (`GZ2E01`) or European/PAL (`GZ2P01`), as an `.iso` or a compressed `.rvz`. Wii Twilight Princess is a different game and isn't supported.
2. On first launch the app opens a disc-picker screen and waits until it sees a valid disc.
3. In the Files app, drop the image into *On My Apple Vision Pro → Dusklight → Documents*.

The disc is read directly at runtime; nothing is extracted, converted, or copied off your device. Memory-card saves and settings are kept when you swap discs.

Dolphin-format HD/4K texture packs are optional; see [Texture packs](README.md#texture-packs) in the README.

## Install the prebuilt app

1. Install SideStore on the headset with the [iloader fork for visionOS](https://github.com/rebelancap/iloader/releases#release-visionos). It runs on an Apple Silicon Mac and pairs with the headset over Wi-Fi: no cable, no Dev Strap, no Xcode.
2. In SideStore, go to *Sources → +* and paste this source, then install Dusklight:

   ```
   https://raw.githubusercontent.com/rebelancap/dusklight-ios/main/sidestore/apps-visionos.json
   ```

   The app updates from this source when new versions ship.

To install by hand instead, download `dusklight-*-visionOS.ipa` from the [latest release](https://github.com/rebelancap/dusklight-ios/releases/latest) and install it through SideStore.

## Build from source

From a checkout of this repo:

```sh
scripts/bootstrap.sh           # clone upstream @ pinned commit + apply the overlay
scripts/build-visionos.sh      # Apple Vision Pro device build
```

`scripts/build-visionos.sh` writes the app to `spikes/vision-device-build/Dusklight.app`. It does not code-sign the app. The first build is slow: Dawn has no prebuilt xrOS package and is compiled from source (about 2000 objects). Later builds are incremental.

Upstream Dusklight is vendored unmodified and pinned by commit; `bootstrap.sh` fetches it. Every local change is an overlay patch in `overlay/patches/`, applied by `scripts/apply-overlay.sh`. The visionOS app shell lives in `app/visionos/`. A Simulator build is in [Building from source](README.md#building-from-source).

## Notes

- The 3D mode has live settings for stereo depth, screen size, distance and height, and surroundings dimming. Press the Digital Crown to recenter the screen and sound together.
- Apps sideloaded with a free Apple account expire after 7 days (paid developer accounts last a year). SideStore refreshes them in the background; if the app stops launching, open SideStore and let it re-sign.
- If the app crashes, it writes `crash.txt` and a `logs/` folder to *On My Apple Vision Pro → Dusklight* in Files. Attach them to a [GitHub issue](https://github.com/rebelancap/dusklight-ios/issues).
- THE LEGEND OF ZELDA: TWILIGHT PRINCESS is © Nintendo. This project is not affiliated with or endorsed by Nintendo, and ships no Nintendo content.
