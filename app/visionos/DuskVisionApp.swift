// DuskVisionApp.swift — SwiftUI app entry for the visionOS target (Phase 2).
//
// visionOS requires a SwiftUI `App` to declare an `ImmersiveSpace` (UIKit can't
// open one), so the app entry is SwiftUI — but it only HOSTS the existing
// SDL3/aurora/Dusklight engine (DuskHostViewController boots it) in a WindowGroup
// and declares the ImmersiveSpace for the stereo "3D screen". All engine/shell
// logic stays in C/C++/ObjC; this file is scene plumbing only. Adapted from the
// proven Shipwright-ios SohVisionApp.swift (STEREO-3D-GUIDE §1 + §5 + §7).
//
// M1: this whole shell is present and 2D boots under it; the ornament pill + the
// settings sheet render and work. The immersive path (Dusk_Enter3D) is a no-op in
// M1 (DuskHostViewController.m) so the space is never opened — M2 fills it in.

import SwiftUI
import CompositorServices
import AVFAudio
import UIKit

final class DuskAppModel: ObservableObject {
    static let shared = DuskAppModel()
    @Published var immersive = false
    @Published var showSettings = false
}

// Called from ObjC (DuskHostViewController) to flip the SwiftUI state that
// actually opens/dismisses the space.
@_cdecl("Dusk_SetImmersiveMode")
func Dusk_SetImmersiveMode(_ on: Bool) {
    DispatchQueue.main.async { DuskAppModel.shared.immersive = on }
}

// Sound-stage anchoring — Fable FOLLOWUP §A 3-probe discriminator. The round-3 .front
// toggle worked for ~0.25s then the system reverted it to the window scene, so we test
// three modes live (settings picker "vp3dAudioMode") + log the audio notifications to
// see if the revert is catchable:
//   * "front"     — .headTracked/.front, MOVE toggle on entry/recenter, ASSERT on timer.
//   * "fixed"     — .fixed head-locked stereo (rides with the head like headphones;
//                   eliminates "from the left" by construction if it persists).
//   * "scene"     — .headTracked anchored to the immersive space's SCENE (may be stickier).
private func duskAudioMode() -> String {
    UserDefaults.standard.string(forKey: "vp3dAudioMode") ?? "front"
}

// Log every connected scene + return the immersive space's persistentIdentifier (best
// effort) for the .scene probe.
private func duskImmersiveSceneId() -> String? {
    var found: String? = nil
    for scene in UIApplication.shared.connectedScenes {
        let id = scene.session.persistentIdentifier
        let cls = String(describing: type(of: scene))
        NSLog("[Dusk3D] audio: scene id=\(id) role=\(scene.session.role.rawValue) class=\(cls)")
        if scene.session.role.rawValue.contains("Immersive") || cls.contains("Immersive") {
            found = id
        }
    }
    return found
}

// Apply the current audio mode. `move` matters only for "front" (toggle vs plain assert).
private func duskApplyAudio(move: Bool) {
    let session = AVAudioSession.sharedInstance()
    do {
        if session.category != .playback {
            try session.setCategory(.playback, mode: .default)
        }
        switch duskAudioMode() {
        case "fixed":
            try session.setIntendedSpatialExperience(.fixed(soundStageSize: .medium))
            DuskIos_SetAudioAnchorStatus(1)
            NSLog("[Dusk3D] audio: FIXED (head-locked)")
        case "scene":
            if let id = duskImmersiveSceneId() {
                try session.setIntendedSpatialExperience(
                    .headTracked(soundStageSize: .medium, anchoringStrategy: .scene(identifier: id)))
                DuskIos_SetAudioAnchorStatus(1)
                NSLog("[Dusk3D] audio: SCENE \(id)")
            } else {
                try session.setIntendedSpatialExperience(
                    .headTracked(soundStageSize: .medium, anchoringStrategy: .front))
                NSLog("[Dusk3D] audio: SCENE fallback .front (no immersive scene id found)")
            }
        default: // "front"
            if move {
                try session.setIntendedSpatialExperience(
                    .headTracked(soundStageSize: .automatic, anchoringStrategy: .automatic))
                NSLog("[Dusk3D] audio: FRONT MOVE released; re-anchor in 0.2s (cat=\(session.category.rawValue))")
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                    do {
                        try AVAudioSession.sharedInstance().setIntendedSpatialExperience(
                            .headTracked(soundStageSize: .medium, anchoringStrategy: .front))
                        DuskIos_SetAudioAnchorStatus(1)
                        NSLog("[Dusk3D] audio: FRONT re-anchored")
                    } catch {
                        NSLog("[Dusk3D] audio: FRONT re-anchor failed: \(error)")
                        DuskIos_SetAudioAnchorStatus(2)
                    }
                }
            } else {
                try session.setIntendedSpatialExperience(
                    .headTracked(soundStageSize: .medium, anchoringStrategy: .front))
                DuskIos_SetAudioAnchorStatus(1)
            }
        }
    } catch {
        NSLog("[Dusk3D] audio: apply failed (mode=\(duskAudioMode())): \(error)")
        DuskIos_SetAudioAnchorStatus(2)
    }
}

private func duskSetAudioAutomatic() {
    try? AVAudioSession.sharedInstance().setIntendedSpatialExperience(
        .headTracked(soundStageSize: .automatic, anchoringStrategy: .automatic))
    NSLog("[Dusk3D] audio: -> automatic")
}

// Probe 1: log the audio notifications so a device session shows whether ANY event
// fires at the ~0.25s revert (if none, the revert is uncatchable arbitration -> §4).
private var duskAudioObservers: [NSObjectProtocol] = []
private func duskStartAudioObservers() {
    guard duskAudioObservers.isEmpty else { return }
    let nc = NotificationCenter.default
    let names: [Notification.Name] = [
        AVAudioSession.routeChangeNotification,
        AVAudioSession.interruptionNotification,
        AVAudioSession.mediaServicesWereResetNotification,
        AVAudioSession.silenceSecondaryAudioHintNotification,
    ]
    for n in names {
        duskAudioObservers.append(nc.addObserver(forName: n, object: nil, queue: .main) { _ in
            NSLog("[Dusk3D] audio: NOTE \(n.rawValue)")
        })
    }
}

private var duskAudioTimer: Timer?
private func duskStartAudioReanchor() {
    duskStopAudioReanchor()
    duskStartAudioObservers()
    duskApplyAudio(move: true) // entry: anchor the stage (front=MOVE onto panel; fixed/scene=set)
    // Timer is ASSERT-only (guard against SDL churn), never a MOVE for "front".
    duskAudioTimer = Timer.scheduledTimer(withTimeInterval: 3.0, repeats: true) { _ in
        duskApplyAudio(move: false)
    }
}
private func duskStopAudioReanchor() {
    duskAudioTimer?.invalidate()
    duskAudioTimer = nil
}

struct DuskWindowView: UIViewControllerRepresentable {
    func makeUIViewController(context: Context) -> DuskHostViewController {
        return DuskHostViewController()
    }
    func updateUIViewController(_ vc: DuskHostViewController, context: Context) {}
}

// Query capabilities so we never request an unsupported combination (which
// makes openImmersiveSpace fail with a generic .error).
struct DuskCompositorConfiguration: CompositorLayerConfiguration {
    func makeConfiguration(capabilities: LayerRenderer.Capabilities,
                           configuration: inout LayerRenderer.Configuration) {
        let layouts = capabilities.supportedLayouts(options: [])
        // Foveation (VISIONOS-FOVEATION-GUIDE): an eye-tracked variable-density drawable makes
        // the 3D panel de-blur to ~2D sharpness (SoH device-validated, confirmed on device "a
        // million times better"). The vkQuake "foveation OFF" line was a Vulkan-era constraint
        // that doesn't apply to our native-Metal pass. Always on where supported.
        let fov = capabilities.supportsFoveation
        // TRAP 1: layered layout + per-slice passes + foveation = right-eye fisheye. Dedicated
        // gives per-EYE texture AND per-eye rate map, which our per-slice render loop needs.
        if fov && layouts.contains(.dedicated) {
            configuration.layout = .dedicated
        } else {
            configuration.layout = layouts.contains(.layered) ? .layered : .dedicated
        }
        // TRAP 2: do NOT touch maxRenderQuality -- raising it aborts at immersive entry. Leave default.
        configuration.colorFormat = capabilities.supportedColorFormats.first ?? .bgra8Unorm_srgb
        configuration.depthFormat = capabilities.supportedDepthFormats.first ?? .depth32Float
        NSLog("[Dusk3D] Swift: compositor configured (foveation=\(fov) layout=\(configuration.layout == .dedicated ? "dedicated" : "layered"))")
    }
}

// Live 3D-panel settings (persisted in UserDefaults, pushed to the loop's
// setters both on change and on immersive entry). Keys/defaults/ranges per
// STEREO-3D-GUIDE §1.3.
struct Dusk3DSettingsView: View {
    @AppStorage("vp3dDist") private var dist = 3.6
    @AppStorage("vp3dHalfW") private var halfW = 2.606  // 17.1 ft wide (default; freely adjustable)
    @AppStorage("vp3dHalfH") private var halfH = 1.798  // 11.8 ft tall
    @AppStorage("vp3dHeight") private var height = 0.0
    @AppStorage("vp3dDim") private var dim = 0.8
    @AppStorage("vp3dDepth200") private var depth200 = 100.0 // tuned default; the one stereo-depth knob
    @AppStorage("vp3dAudioMode") private var audioMode = "front" // Fable §A probe: front/fixed/scene
    @AppStorage("vp3dUseFeet") private var useFeet = false
    @AppStorage("vp3dProbe") private var probe = false           // WATER-ANSWER §D false-color probe
    // Sprite flatten BAKED (no UI): mode 5 "Both" (No-Z butterflies + Cutout ground specks),
    // size limit 4 verts. The pumpkin-shadow companion shares the No-Z class and can't be
    // isolated (per sub-mode testing), so its residual is accepted. applyAll applies these.
    // Baked (locked in — no sliders): far depth 0.5, texture/water 0.5, water-only
    // texcoord shift, near-limit softplus 2.0. applyAll() still applies them from UserDefaults.

    static func resetAll() {
        let d = UserDefaults.standard
        d.set(3.6, forKey: "vp3dDist"); d.set(2.606, forKey: "vp3dHalfW")
        d.set(1.798, forKey: "vp3dHalfH"); d.set(0.0, forKey: "vp3dHeight")
        d.set(0.8, forKey: "vp3dDim")
        d.set(3.0, forKey: "vp3dSharp")
        d.set(100.0, forKey: "vp3dDepth200")
        d.set(0.5, forKey: "vp3dFarDepth")
        d.set(1.0, forKey: "vp3dNearDepth")
        d.set(0.50, forKey: "vp3dTexAmt")
        d.set(2.0, forKey: "vp3dKGrad")
        applyAll()
        applyPanelAspect() // Reset must re-apply the aspect too, else the panel stays distorted
    }

    static func applyAll() {
        let d = UserDefaults.standard
        // One-time: adopt the tuned defaults on existing installs (Depth 100%, Texture
        // depth 43%) without wiping other settings. Runs once, then respects his changes.
        if d.integer(forKey: "vp3dCfgVer") < 1 {
            d.set(100.0, forKey: "vp3dDepth200")
            d.set(0.43, forKey: "vp3dTexAmt")
        }
        if d.integer(forKey: "vp3dCfgVer") < 2 {
            // Subject-anchored convergence (Fable) shrinks near disparity ~4-6x, so the near
            // clamp goes back tight -- reset the 400% workaround to 100%; walk it down from there.
            d.set(1.0, forKey: "vp3dNearDepth")
            d.set(2, forKey: "vp3dCfgVer")
        }
        if d.integer(forKey: "vp3dCfgVer") < 3 {
            // No-Z is the doubling sprite class -> default the sprite-flatten to it.
            d.set(2, forKey: "vp3dFlatMode")
            d.set(3, forKey: "vp3dCfgVer")
        }
        if d.integer(forKey: "vp3dCfgVer") < 4 {
            // Size limit 4 (single-quad sprites) fuses butterflies/flowers, spares paths.
            d.set(4, forKey: "vp3dFlatMaxVtx")
            d.set(4, forKey: "vp3dCfgVer")
        }
        if d.integer(forKey: "vp3dCfgVer") < 5 {
            // TEXGEN-SPLIT (D-059) made Texture depth act on the water ALONE, so the old 43%
            // all-layers compromise no longer applies -- 50% is the magic number now.
            d.set(0.50, forKey: "vp3dTexAmt")
            d.set(5, forKey: "vp3dCfgVer")
        }
        if d.integer(forKey: "vp3dCfgVer") < 7 {
            // NEAR-FIELD (D-063/064): softplus dz-floor solely bounds the near disparity + its
            // gradient (kills the V-tear). 200% locked in as the fix -> bake it, slider removed.
            d.set(2.0, forKey: "vp3dKGrad")
            d.set(7, forKey: "vp3dCfgVer")
        }
        if d.integer(forKey: "vp3dCfgVer") < 8 {
            // Sharpness 3.0 is the sweet spot (higher hurts fps + adds motion blur, lower
            // looks bad). Bake it, slider removed.
            d.set(3.0, forKey: "vp3dSharp")
            d.set(8, forKey: "vp3dCfgVer")
        }
        if d.integer(forKey: "vp3dCfgVer") < 9 {
            // The ground specks (pumpkin shadows, dirt) are Cutout (alpha-test) and
            // the butterflies are No-Z -- two classes. "Both" (mode 5) flattens them together.
            d.set(5, forKey: "vp3dFlatMode")
            d.set(9, forKey: "vp3dCfgVer")
        }
        if d.integer(forKey: "vp3dCfgVer") < 10 {
            // Sprite flatten baked + picker removed. FORCE mode 5: an install that was left on a
            // now-deleted test mode (6/7/8) would otherwise flatten nothing, with no picker to fix it.
            d.set(5, forKey: "vp3dFlatMode")
            d.set(4, forKey: "vp3dFlatMaxVtx")
            d.set(10, forKey: "vp3dCfgVer")
        }
        func f(_ k: String, _ def: Double) -> Float {
            return Float(d.object(forKey: k) != nil ? d.double(forKey: k) : def)
        }
        Dusk3D_SetPanel(f("vp3dDist", 3.6), f("vp3dHalfW", 2.606), f("vp3dHalfH", 1.798))
        Dusk3D_SetHeight(f("vp3dHeight", 0.0))
        Dusk3D_SetDim(f("vp3dDim", 0.8))
        // Render-supersample (crispness on the panel). Gated to 3D inside the
        // shell setter -- a no-op while the 2D window is onscreen.
        Dusk_SetSharpness(f("vp3dSharp", 3.0))
        // Stereo depth = the eye-separation fraction; aurora scales it by the adaptive
        // convergence distance. Rescaled per device tuning: the comfortable
        // MAX (0.0325) is the slider's 200% (past it = too intense/blurry); default 30%.
        Dusk3D_SetStereoParams(f("vp3dDepth200", 100.0) / 200.0 * 0.0325, 1.0, f("vp3dFarDepth", 0.5), f("vp3dNearDepth", 1.0))
        Dusk3D_SetKGrad(f("vp3dKGrad", 2.0))
        Dusk3D_SetStereoDebug(d.bool(forKey: "vp3dProbe") ? 1 : 0)
        Dusk3D_SetTexAmt(f("vp3dTexAmt", 0.50))
        Dusk3D_SetTxsAll(UserDefaults.standard.bool(forKey: "vp3dTxsAll") ? 1 : 0)
        Dusk3D_SetFlatMode(Int32(d.object(forKey: "vp3dFlatMode") != nil ? d.integer(forKey: "vp3dFlatMode") : 5))
        Dusk3D_SetFlatMaxVtx(Int32(d.object(forKey: "vp3dFlatMaxVtx") != nil ? d.integer(forKey: "vp3dFlatMaxVtx") : 4))
    }

    // Panel aspect = width/height ratio -> the game renders AT that aspect (undistorted,
    // like 2D). Applied on slider RELEASE + on 3D entry, NOT live: it resizes the render
    // target, so a live drag would resize-storm (the quad still tracks size live).
    static func applyPanelAspect() {
        let d = UserDefaults.standard
        func f(_ k: String, _ def: Double) -> Double { d.object(forKey: k) != nil ? d.double(forKey: k) : def }
        let hw = f("vp3dHalfW", 2.606), hh = f("vp3dHalfH", 1.798)
        Dusk_SetPanelAspect(Float(hh > 0.01 ? hw / hh : 1.45))
    }

    private func fmt(_ meters: Double) -> String {
        return useFeet ? String(format: "%.1f ft", meters * 3.28084)
                       : String(format: "%.1f m", meters)
    }

    var body: some View {
        Form {
            Section("Screen") {
                LabeledContent("Distance  \(fmt(dist))") {
                    Slider(value: $dist, in: 1.0...8.0)
                }
                LabeledContent("Width  \(fmt(halfW * 2))") {
                    // Panel aspect updates LIVE now (it's just a float the render loop reads),
                    // so the picture never distorts mid-drag and Reset re-applies it too.
                    Slider(value: $halfW, in: 0.6...4.0)
                }
                LabeledContent("Height  \(fmt(halfH * 2))") {
                    Slider(value: $halfH, in: 0.4...3.0)
                }
                LabeledContent("Position height  \(fmt(height))") {
                    Slider(value: $height, in: -1.5...5.0)
                }
                LabeledContent("Units") {
                    Picker("", selection: $useFeet) {
                        Text("m").tag(false)
                        Text("ft").tag(true)
                    }
                    .pickerStyle(.segmented)
                    .frame(maxWidth: 180)
                }
            }
            Section("Stereo Depth") {
                LabeledContent("Depth  \(Int(depth200))%") {
                    Slider(value: $depth200, in: 0.0...200.0)
                }
                Text("How much 3D pop. Start low and raise it until the depth feels right without eye strain. Far depth, water texture depth, and the near-ground limit are now tuned in and fixed.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            Section("Surroundings") {
                LabeledContent("Dim  \(Int(dim * 100))%") {
                    Slider(value: $dim, in: 0.0...1.0)
                }
            }
            Section("Audio") {
                Picker("Sound", selection: $audioMode) {
                    Text("From screen").tag("front")
                    Text("Head-locked").tag("fixed")
                }
                .pickerStyle(.segmented)
                Text("From screen: sound comes from the panel's direction (press the Digital Crown to recenter both). Head-locked: sound rides with your head like headphones.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            Section {
                Text("Press the Digital Crown to recenter the screen and sound together.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            Section("Diagnostics") {
                Toggle("Water probe (false-color)", isOn: $probe)
                Text("Tints the world by depth type so we can find the water: red = flat HUD, green = 3D surfaces that move correctly, blue = projected texture layers (the suspected water shine). Look at a lake — blue/green tells us the fix. Turn off to play.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .onChange(of: probe) { Dusk3D_SetStereoDebug(probe ? 1 : 0) }
        .onChange(of: audioMode) { duskApplyAudio(move: true) }
        .onChange(of: dist) { Self.applyAll() }
        .onChange(of: halfW) { Self.applyAll(); Self.applyPanelAspect() }
        .onChange(of: halfH) { Self.applyAll(); Self.applyPanelAspect() }
        .onChange(of: height) { Self.applyAll() }
        .onChange(of: dim) { Self.applyAll() }
        .onChange(of: depth200) { Self.applyAll() }
    }
}

struct DuskRootView: View {
    @ObservedObject private var model = DuskAppModel.shared
    @Environment(\.openImmersiveSpace) private var openImmersiveSpace
    @Environment(\.dismissImmersiveSpace) private var dismissImmersiveSpace

    var body: some View {
        DuskWindowView()
            .ignoresSafeArea()
            // 3D + settings in a BOTTOM ornament, pushed fully BELOW the window
            // (the default centered ornament straddles the boundary and overlaps
            // game content). Guide §1.1.
            .ornament(attachmentAnchor: .scene(.bottom), contentAlignment: .top) {
                HStack(spacing: 16) {
                    Button(model.immersive ? "Exit 3D" : "3D") {
                        Dusk_Enter3D(!model.immersive)
                    }
                    Button {
                        model.showSettings = true
                    } label: {
                        Image(systemName: "gearshape.fill")
                    }
                }
                .font(.title3)
                .buttonStyle(.borderless)
                .padding(.horizontal, 20)
                .padding(.vertical, 12)
                .glassBackgroundEffect()
                .opacity(0.85)
                .padding(.top, 14)
            }
            // SwiftUI sheet (a UIKit modal silently fails over an open
            // ImmersiveSpace). Width-only frame; own Done bar. Guide §1.2.
            .sheet(isPresented: $model.showSettings) {
                VStack(spacing: 0) {
                    HStack {
                        Text("3D Screen Settings").font(.title3.weight(.semibold))
                        Spacer()
                        Button("Reset") { Dusk3DSettingsView.resetAll() }
                            .font(.title3)
                        Button("Done") { model.showSettings = false }
                            .font(.title3)
                            .buttonStyle(.borderedProminent)
                    }
                    .padding(.horizontal, 24)
                    .padding(.vertical, 12)
                    Divider()
                    Dusk3DSettingsView()
                }
                .frame(minWidth: 700)
            }
            .onChange(of: model.immersive) { _, on in
                NSLog("[Dusk3D] Swift: immersive onChange -> \(on)")
                Task {
                    if on {
                        Dusk3DSettingsView.applyAll() // panel state before first frame
                        Dusk3DSettingsView.applyPanelAspect() // render at the panel aspect
                        duskStartAudioReanchor()
                        let r = await openImmersiveSpace(id: "Dusk3D")
                        NSLog("[Dusk3D] Swift: openImmersiveSpace -> \(String(describing: r))")
                        if case .error = r {
                            Dusk_Enter3D(false) // roll back engine offscreen mode
                        } else {
                            duskApplyAudio(move: true) // anchor once the space is fully open + panel placed
                        }
                    } else {
                        await dismissImmersiveSpace()
                        NSLog("[Dusk3D] Swift: dismissed immersive")
                        duskStopAudioReanchor()
                        duskSetAudioAutomatic()
                        // The window never deactivates under mixed immersion —
                        // this is the authoritative back-to-2D trigger.
                        Dusk_Exit3DFinalize()
                    }
                }
            }
    }
}

@main
struct DuskVisionApp: App {
    @ObservedObject private var model = DuskAppModel.shared
    var body: some Scene {
        WindowGroup {
            DuskRootView()
        }
        ImmersiveSpace(id: "Dusk3D") {
            CompositorLayer(configuration: DuskCompositorConfiguration()) { layerRenderer in
                // This closure runs on the MAIN thread; the frame loop must NOT
                // (it would block the engine's frame pump).
                NSLog("[Dusk3D] Swift: CompositorLayer ready — spawning render thread")
                let renderThread = Thread { Dusk3D_Immersive_Run(layerRenderer) }
                renderThread.name = "Dusk3D-Immersive"
                renderThread.stackSize = 2 << 20
                renderThread.start()
            }
        }
        // Mixed = panel floats in passthrough. Merely ALLOWING .progressive
        // changes the drawable contract and encode_present aborts (guide §9.1 #1).
        .immersionStyle(selection: .constant(.mixed), in: .mixed)
    }
}
