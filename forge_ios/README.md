# Forge iOS

The iPhone client for the **Forge** Gemini Live agent. Built native on ARKit + RealityKit + Vision + SwiftUI — no Unity, no Flutter, no cross-language bridge.

> Companion to the Quest app. Same orchestrator, same wire protocol, same audit log — only the body changes. World-locked outlines pinned to the chips on your bench through your iPhone's camera, voice-controlled debug session via Gemini Live, expert-agent chat panel on the right.

See [`IMPLEMENTATION.md`](IMPLEMENTATION.md) for the full module spec.

## Project status

Scaffolding underway. The spec is frozen; module subagents have not yet been dispatched. This is the second client; the Quest client (`../forge_quest/`) is already built and the orchestrator (`../forge_orchestrator/`) is the shared backend.

- Wire contract (`Proto/AgentProto.swift`) mirrors `forge_orchestrator/forge_orchestrator/proto/events.py` 1:1.
- Eight Phase-1 module tasks (Net, Camera, Audio, Spatial, Vision, Scene, Input, State) are defined in `IMPLEMENTATION.md` and ready to fan out to parallel agents.
- The Day-0 spike (`Dev/SpikeView.swift`) is the make-or-break canary — verifies ARKit + LiDAR + WS hello in under 5 seconds on real hardware.
- What's left for the demo: build out the eight modules, wire `SessionViewModel`, point at a running orchestrator, run the spike on a real iPhone Pro.

## Requirements

- **iPhone 12 Pro / 13 Pro / 14 Pro / 15 Pro / 16 Pro** (or iPad Pro 2020+) — LiDAR sensor required
- **iOS 17.0+** (iOS 18 enables the preferred `RealityView` rendering path)
- **Xcode 15.4+** — Swift 5.10, iOS 17 SDK
- macOS Sonoma 14.4+ (for Xcode 15.4)
- Apple Developer account — free side-load works for a 7-day demo; paid for anything longer
- Same LAN as the orchestrator host (or a tunnelled public URL)

## Build & install

```bash
# from forge_ios/
xcodebuild -project Forge.xcodeproj \
    -scheme Forge \
    -configuration Debug \
    -destination 'platform=iOS,name=Your iPhone' \
    -allowProvisioningUpdates \
    clean build install
```

Or just open `Forge.xcodeproj` in Xcode, select your device, ⌘R.

### Configure orchestrator URL + auth token

Build-time config is pulled from `.xcconfig` overrides, environment variables, or sensible dev defaults (in that order):

```bash
# command-line override:
xcodebuild ... \
    ORCHESTRATOR_URL=wss://your-cloud-run.run.app/v1/session \
    AUTH_TOKEN=$(cat ~/.forge/dev_token)

# or via Forge.xcconfig (gitignored):
ORCHESTRATOR_URL = ws://192.168.1.50:8080/v1/session
AUTH_TOKEN = forge-dev-shared-secret
```

If `AUTH_TOKEN` is unset, the app falls back to `DEV_TOKEN` (default `forge-dev-shared-secret`) so the orchestrator must allow that shared secret in dev mode.

Runtime overrides are available in the in-app `SettingsPanel` (persists to `UserDefaults` suite `ai.forge.ios`).

## Run

Two entry points ship in the app:

```text
# Day-0 spike — proves ARKit + LiDAR + WS hello in <5s
Long-press the Forge app icon → "Spike" (DEBUG builds only)

# Full app
Tap the Forge app icon
```

**On first launch, grant ALL permissions when prompted:**

- Camera (`NSCameraUsageDescription`)
- Microphone (`NSMicrophoneUsageDescription`)
- Local Network (`NSLocalNetworkUsageDescription`) — required to reach the orchestrator over LAN
- Speech Recognition (`NSSpeechRecognitionUsageDescription`) — for local voice intents

If outlines never appear, check Settings → Privacy → Camera → Forge. If the connection banner shows "orchestrator unreachable" but the orchestrator is up, check Local Network permission first.

## Demo flow

1. Mount your iPhone Pro on a goose-neck arm over your electronics bench (or hold it in landscape).
2. Open Forge iOS.
3. Walk it across the bench once — ARKit + LiDAR build the scene mesh in 3–5 seconds.
4. Speak: *"Forge, what do you see on my board?"*
5. Component outlines glow on real chips through the camera feed; labels float at each component's centroid.
6. Tap a component → its expert-agent thread opens in the right-side chat panel (`@U1: this is an STM32F411, you'll want a 100 nF on pin 9`).
7. When the agent calls `set_psu`, a confirmation sheet slides up; tap **Approve** or **Deny** (or say "yes/no").
8. Pick up the board — outlines follow it (LiDAR scene-mesh raycast keeps them glued).

## Project layout

```
forge_ios/
├── README.md                          # you are here
├── IMPLEMENTATION.md                  # the spec subagents read
├── Forge.xcodeproj/
├── Package.swift                      # empty for v1; SPM deps added in Phase 7 if needed
└── Forge/
    ├── ForgeApp.swift                 # @main, App scene, permission gate
    ├── Info.plist
    ├── Assets.xcassets
    ├── Proto/                         # frozen wire contract — AgentProto, Coding, ToolResults
    ├── Net/                           # OrchestratorSocket (URLSessionWebSocketTask)
    ├── Camera/                        # ARKitSession, FrameEncoder, IntrinsicsExtractor
    ├── Audio/                         # MicCapture (AVAudioEngine), SpeakerPlayer, VoiceIntent
    ├── Spatial/                       # AnchorRegistry, Raycaster, SceneMeshQuery, PoseProvider
    ├── Vision/                        # Segmenter, Tracker, MaskPolygonizer
    ├── Scene/                         # ForgeRealityView + ComponentOutline + SwiftUI chrome
    ├── Input/                         # TapInput, PinchInput, VoiceCommandRegistry, InputAction
    ├── State/                         # SessionViewModel, DetectionStore, ChatStore, ReplayBuffer
    └── Dev/SpikeView.swift            # day-0 validation
```

## Module boundaries

Modules under `Forge/` may only import:

- The iOS framework (`Foundation`, `ARKit`, `RealityKit`, `Vision`, `SwiftUI`, `AVFoundation`, `Speech`, `Accelerate`, `simd`)
- Third-party libs declared in `Package.swift` (none for v1)
- `Proto/AgentProto.swift` (shared types)
- Interfaces explicitly listed as cross-module contracts in `IMPLEMENTATION.md` (`SessionViewModel`, `DetectionStore`, `ChatStore` — read-only observers across modules)

`Scene/ForgeRealityView.swift` is the single integration point — it owns the RealityKit entity graph and consumes outputs from every adapter module. No other cross-module imports exist; the boundary check is:

```bash
# from Forge/
grep -rn "^import " . | grep -v "Proto\\|Foundation\\|ARKit\\|RealityKit\\|Vision\\|SwiftUI\\|AVFoundation\\|Speech\\|Accelerate\\|simd\\|Combine\\|Observation\\|CoreML\\|CoreImage"
# expect: no matches except SessionViewModel/DetectionStore/ChatStore observers
```

## Zero-TODO policy

The codebase intentionally contains no `TODO` comments. Configuration that depends on your environment (orchestrator URL, auth tokens, GCP project) is plumbed via `ConfigStore` and falls back to safe dev defaults rather than living as inline TODOs.

```bash
grep -rn "TODO\\|FIXME\\|XXX" Forge/ *.md   # expect: no matches
```

## Day-0 spike

`Dev/SpikeView.swift` is your make-or-break canary. Per the spec:

> If you see "It works." printed AND one bright-green world-locked outline appears within 5 seconds of launch, the spec is good. If either fails, the spec needs a pivot — most likely to non-LiDAR fallback or to the older `ARView` rendering path.

```text
# DEBUG builds only:
Long-press Forge app icon → "Spike"
# expect within 5s in Console.app filtered to subsystem ai.forge.ios:
#   [Spike] ARKit session started, intrinsics fx=1432.1 cx=960.0
#   [Spike] WS hello roundtrip: 87ms
#   [Spike] It works. anchor=Optional("...") world=(0.42, -0.08, -0.65)
```

If no anchor is created, the LiDAR scene mesh hasn't built yet — pan the phone around the bench for 3 seconds and re-run.

## Stub mode

When the orchestrator is unreachable, the app stays usable:

- `SessionViewModel.connection = .degraded(reason: "orchestrator unreachable")`
- A `DegradedStatusPanel` banner appears at the top
- Synthetic `LookAtBenchResult`s emit every 2 s — three fake components labeled "U1 (stub)", "R3 (stub)", "C12 (stub)" at fixed normalized pixel locations
- All other UI continues to work — tap-to-focus, chat history, settings, ARKit overlays

This matches the orchestrator's own stub-mode pattern. The demo loop survives any single component failing.

## Architecture reference

```
┌─────────── ForgeApp ────────────────┐
│   permission gate → RootView         │
│              ↓                        │
│       SessionViewModel                │
└─────┬──────┬─────┬──────┬─────┬─────┘
      ▼      ▼     ▼      ▼     ▼
    Net   Camera Audio Vision Spatial
                                  │
                                  ▼
                                Scene/
                            (RealityView +
                             SwiftUI chrome)
```

`SessionViewModel` owns the lifecycle. `ForgeRealityView` owns the RealityKit + SwiftUI surface and is the only place that touches entities. The other modules are pure adapters with no RealityKit or cross-module dependencies.

## How this relates to forge_quest

The two clients are intentionally symmetric. If you understand `forge_quest/`, you understand this — module names and roles map directly. See the appendix at the bottom of `IMPLEMENTATION.md` for the table.

Key differences:

- **Depth & 3-space:** ARKit gives us a real LiDAR scene mesh; `forge_quest/` uses MRUK plane approximations.
- **Overlays:** RealityKit `ModelEntity` + `Attachment` replaces Spatial SDK `PanelRegistration`.
- **Activity model:** SwiftUI App + `@Observable` ViewModel replaces `AppSystemActivity` + Compose panels.
- **WebSocket:** native `URLSessionWebSocketTask` replaces OkHttp.

The orchestrator does not need to know which client is connected — both speak the same `AgentEvent` envelope.

## License

Internal — Forge / Galois hackathon project, May 2026.
