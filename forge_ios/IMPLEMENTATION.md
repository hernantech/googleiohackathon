# Forge iOS — Implementation Spec

> This is the contract document for parallel subagents implementing the **Forge iOS** client.
> Each task lists the files an agent must create, the public interfaces they must expose, the shared types they may import, and verification criteria.
> Agents working in parallel MUST stay inside their assigned directories and rely only on shared types listed in their task block.

Sibling specs: [`forge_quest/IMPLEMENTATION.md`](../forge_quest/IMPLEMENTATION.md) and [`forge_orchestrator/IMPLEMENTATION.md`](../forge_orchestrator/IMPLEMENTATION.md). The iOS client is a peer of the Quest client — same orchestrator, same wire protocol, same bench daemon. **A user can run either one (or both) against the same Forge session.**

---

## Project context

**What we are building.** A native iOS app that turns an iPhone (with LiDAR) into a multimodal AR lab partner for hands-on electronics work. The user mounts the phone over their workbench (or holds it), opens the app, and talks to a Gemini Live agent through Forge. The agent identifies components on the PCB via the rear camera, returns masks + bounding boxes, and the app renders **world-locked outlines and labels** that stay glued to each real component as the user moves the phone or the board.

**Why iOS / native Swift, not Flutter or Unity.** ARKit, RealityKit, the Vision framework, and Core ML are all first-class Swift APIs. The LiDAR scene mesh, per-pixel depth, ARKit world anchors, and `VNGenerateForegroundInstanceMaskRequest` are all one-line calls. Any cross-language bridge (Flutter platform channels, Unity, React Native) adds a marshalling tax on a high-rate stream (LiDAR depth = ~12 MB/s) without removing the Swift code we'd need anyway. SwiftUI gives us the chat panel and HUD for free; RealityKit gives us the world-locked overlay for free.

**Why the iPhone client at all (we already have Quest).** Two reasons:

1. **Demo surface.** Quest 3 has untested runtime risk (headset-camera permission, tabletop scene mesh, panel sizing) — see `../BUILD_LEARNINGS.md` lines 392–399. iPhone+ARKit has zero of those unknowns; everything is shipped, documented, and Apple-supported. Demos in seconds, not minutes.
2. **A single device for the whole loop.** Camera, display, mic, speaker, on-device ML, *and* USB-C connection to bench instruments live on one device. No second laptop, no headset strap, no separate companion phone. The orchestrator is unchanged.

**The orchestrator (out of scope for this project).** A FastAPI service that proxies Gemini Live and dispatches tool calls. We only need to know the wire protocol; the source of truth is `forge_orchestrator/forge_orchestrator/proto/events.py`. We mirror it 1:1 in Swift `Codable` types — see "Shared types" below.

**Companion clients.** The Quest 3 client (`forge_quest/`) is a peer of this app on the same orchestrator. Both clients speak the same wire protocol. Either client may also be paired with the other in a multi-client session (e.g., one person wearing the Quest while a second viewpoint comes from a mounted iPhone) — this is supported by the orchestrator session-multiplexer and requires no special logic in the iOS client.

---

## Hardware + OS targets

| Field | Value |
|---|---|
| Deployment target | **iOS 17.0** |
| Minimum device | **iPhone 12 Pro** (LiDAR + A14) or **iPad Pro 2020+** |
| Recommended device | iPhone 15 Pro or 16 Pro (USB-C, Neural Engine throughput for Core ML) |
| Required capabilities | Rear camera, microphone, **LiDAR sensor**, ARKit, Neural Engine |
| Required entitlements | `com.apple.developer.arkit`, `NSCameraUsageDescription`, `NSMicrophoneUsageDescription`, `NSLocalNetworkUsageDescription` (orchestrator on LAN) |
| Orientation | Landscape primary, portrait supported (the AR view is orientation-agnostic; chrome adapts) |

Reasons for the iOS 17 floor:

- `VNGenerateForegroundInstanceMaskRequest` (Vision framework instance segmentation) is iOS 17+.
- `ARFrame.sceneDepth.confidenceMap` and the LiDAR-driven `sceneReconstruction = .meshWithClassification` are best on iOS 17.
- SwiftUI `Observable` macro + `@Observable` view models are iOS 17+ (cleans up `SessionViewModel` significantly).

If `RealityView` (iOS 18+) is available at build time, prefer it over `ARView`; otherwise fall back to `ARView` via `UIViewRepresentable`. See Task 5.

---

## Hard rules for all subagents

1. **Stay inside your assigned directories.** Do not edit files in other modules.
2. **Do not modify `Proto/AgentProto.swift`** — it is the wire contract and is frozen.
3. **Do not modify `Forge.xcodeproj/`, `Package.swift`, or `Info.plist`.** If you need a new dependency, list it in the deliverable notes; the integration phase will add it.
4. **Use Swift Concurrency (async/await + AsyncSequence)**, not Combine, not callback closures, for async APIs. The only allowed Combine entry point is `ObservableObject` for legacy bridging — prefer `@Observable` macro.
5. **No `Task.detached` outside top-level entry points.** Structured concurrency only.
6. **App module**: `Forge`. Files live under `Forge/<Module>/`. The Xcode target is `Forge`; bundle id `ai.forge.ios`.
7. **Comments are sparse.** Only explain non-obvious *why*, not what. Do not include docstrings on every function.
8. **ARKit + RealityKit are pinned to iOS 17 SDK** (Xcode 15.4+). All ARKit / RealityKit / Vision API surfaces used by this project must compile against the iOS 17 SDK; iOS 18+ APIs (e.g. `RealityView`, `LowLevelMesh`) are opt-in behind `#available(iOS 18.0, *)` guards.
9. **No tests in this phase.** Hackathon scope.
10. **Each file you create must compile in isolation** assuming the shared types listed in your task block exist.
11. **Match the orchestrator wire protocol *exactly*.** When in doubt, copy field-by-field from `forge_orchestrator/forge_orchestrator/proto/events.py` and `forge_quest/app/src/main/java/ai/forge/quest/proto/AgentProto.kt`. JSON field names are camelCase on the wire.

---

## Shared types (frozen — DO NOT modify)

These live in `Forge/Proto/AgentProto.swift` and are shipped by Phase 0. All modules import via `import Foundation` + the implicit app target — no module boundary inside the app.

```swift
import Foundation

// MARK: - Wire envelope

enum AgentEvent: Codable, Equatable {
    case transcript(text: String, partial: Bool, ts: Int64)
    case toolCall(name: String, argsJson: String, callId: String)
    case toolResult(callId: String, resultJson: String)
    case confirmationRequest(callId: String, summary: String, risk: Risk)
    case confirmationResponse(callId: String, approved: Bool)
    case audioChunk(pcmBase64: String, ts: Int64)
    case hello(client: String, sessionId: String)
    case goodbye(reason: String)
    // Discriminator key: "kind"
}

enum Risk: String, Codable { case low = "LOW", medium = "MEDIUM", high = "HIGH" }

// MARK: - Camera & audio chunks (binary, sent on a separate WS frame)

struct FrameChunk {
    let jpegBytes: Data
    let widthPx: Int
    let heightPx: Int
    let timestampNs: Int64
}

struct AudioInChunk {
    let pcm: Data            // 16 kHz mono signed 16-bit little-endian
    let timestampNs: Int64
}

// MARK: - Intrinsics (per-session)

struct CameraIntrinsics: Codable, Equatable {
    let focalLengthPx: SIMD2<Float>     // (fx, fy)
    let principalPointPx: SIMD2<Float>  // (cx, cy)
    let distortionCoeffs: [Float]       // radial k1..k4, or empty if rectified
    let imageSizePx: SIMD2<Int32>       // (width, height)
}

// MARK: - Detections (returned by the orchestrator's look_at_bench tool)

struct Bbox2D: Codable, Equatable, Hashable {
    let x1: Int32; let y1: Int32; let x2: Int32; let y2: Int32
    var centerPx: SIMD2<Float> { SIMD2(Float(x1 + x2) / 2, Float(y1 + y2) / 2) }
}

struct DetectedComponent: Codable, Equatable, Hashable {
    let id: String              // stable per-component identifier (e.g. "U1")
    let partNumber: String      // human-readable label (e.g. "STM32F411")
    let bbox: Bbox2D
    let confidence: Float
    let secondary: String?

    // Phase 2 — added when the orchestrator emits segmentation polygons:
    let maskPolygon: [SIMD2<Float>]?   // image-space contour, normalized 0..1; nil for bbox-only
}

// MARK: - Pose

struct Pose6dof: Equatable {
    let positionM: SIMD3<Float>
    let orientationQuat: simd_quatf     // (x, y, z, w)
    let timestampNs: Int64
}
```

The `AgentEvent` JSON wire format uses a `"kind"` discriminator field, identical to the Quest client and the Python orchestrator. Encoders/decoders are hand-written in `Proto/AgentProtoCoding.swift` (Phase 0).

---

## High-level architecture

```
┌──────────────────────────── ForgeApp (SwiftUI) ─────────────────────────┐
│  permission gate → mount ForgeRealityView → init SessionViewModel        │
└─────────┬─────────────────────┬─────────────────────┬──────────────────┘
          │                     │                     │
          ▼                     ▼                     ▼
   ┌───────────────┐     ┌──────────────────┐    ┌──────────────────┐
   │ ForgeReality  │     │ SessionViewModel │    │ PermissionGate   │
   │ View (Scene/) │     │  (State/)        │    │ (App/)           │
   └───────┬───────┘     └────────┬─────────┘    └──────────────────┘
           │                       │
           │ entities & anchors    │ events
           │                       │
           ▼                       ▼
   ┌────────────────┐      ┌────────────────────────┐
   │ ARKitSession   │◄────►│ OrchestratorSocket     │
   │ (Camera/)      │      │ (Net/)                 │
   └────────┬───────┘      └────────────────────────┘
            │                       ▲
            │ frames + depth        │ json + binary
            │                       │
            ▼                       │
   ┌────────────────┐               │
   │ Segmenter      │───────────────┘
   │ (Vision/)      │  detections
   └────────────────┘
            │
            ▼
   ┌────────────────┐
   │ Tracker        │  inter-detection mask warp
   │ (Vision/)      │
   └────────────────┘

   ┌──────────────┐   ┌──────────────────┐   ┌─────────────────┐
   │ MicCapture   │   │ SpeakerPlayer    │   │ ReplayBuffer    │
   │ (Audio/)     │   │ (Audio/)         │   │ (State/)        │
   └──────────────┘   └──────────────────┘   └─────────────────┘
```

Data flow per frame:

1. `ARKitSession` produces an `ARFrame` containing `capturedImage`, `sceneDepth`, `camera.intrinsics`, and `camera.transform`.
2. Every ~3 s OR on voice trigger, the most recent JPEG-encoded frame is uploaded via `OrchestratorSocket` → Gemini Vision → returns a `LookAtBenchResult` (list of `DetectedComponent`).
3. Between Gemini refreshes, `Segmenter` (on-device Vision/Core ML) produces fast masks; `Tracker` warps the last known polygons by optical flow.
4. For each polygon, `Scene/ComponentOutline` raycasts the polygon vertices into world space using ARKit's scene mesh and creates / updates a `ModelEntity` outline anchored to an `ARAnchor`.
5. For each component, `Scene/ComponentLabel` (SwiftUI view embedded via `Attachment`) renders the agent-authored label and the leader line.
6. User taps a component → `Input/TapInput` sends `AgentEvent.toolCall(name: "expert_chat.focus", argsJson: ...)` to the orchestrator → expert chat panel scrolls to that thread.

---

## Directory layout

```
forge_ios/
├── README.md
├── IMPLEMENTATION.md                  # this file
├── Forge.xcodeproj/
├── Package.swift                      # SPM, if any third-party deps; empty for v1
├── Forge/
│   ├── ForgeApp.swift                 # @main, App scene, permission gate
│   ├── Info.plist
│   ├── Assets.xcassets
│   ├── Proto/
│   │   ├── AgentProto.swift           # frozen wire types (Phase 0)
│   │   ├── AgentProtoCoding.swift     # Codable conformance, discriminator
│   │   └── ToolResults.swift          # tool-result payload structs
│   ├── Net/
│   │   ├── OrchestratorSocket.swift   # URLSessionWebSocketTask wrapper
│   │   └── BackoffPolicy.swift
│   ├── Camera/
│   │   ├── ARKitSession.swift         # ARSession lifecycle, frame pump
│   │   ├── FrameEncoder.swift         # JPEG encode CVPixelBuffer
│   │   └── IntrinsicsExtractor.swift  # ARFrame.camera → CameraIntrinsics
│   ├── Audio/
│   │   ├── MicCapture.swift           # AVAudioEngine input tap
│   │   ├── SpeakerPlayer.swift        # AVAudioEngine output
│   │   └── VoiceIntent.swift          # local wake-word / push-to-talk
│   ├── Spatial/
│   │   ├── AnchorRegistry.swift       # stable id ↔ ARAnchor
│   │   ├── Raycaster.swift            # image pixel → world ray (ARFrame)
│   │   ├── SceneMeshQuery.swift       # ray ↔ ARMeshAnchor.geometry hit
│   │   └── PoseProvider.swift         # AsyncSequence<Pose6dof>
│   ├── Vision/
│   │   ├── Segmenter.swift            # VNGenerateForegroundInstanceMaskRequest + Core ML
│   │   ├── Tracker.swift              # KLT optical-flow polygon warp
│   │   └── MaskPolygonizer.swift      # bitmap mask → polygon contour
│   ├── Scene/
│   │   ├── ForgeRealityView.swift     # SwiftUI RealityView/ARView host
│   │   ├── ComponentOutline.swift     # ModelEntity per detection (world-locked)
│   │   ├── ComponentLabel.swift       # SwiftUI label + leader line via Attachment
│   │   ├── ComponentDetailCard.swift  # tap-to-expand detail panel
│   │   ├── ExpertChatPanel.swift      # Discord-style multi-agent thread
│   │   ├── HudOverlay.swift           # top-left status bar
│   │   ├── ConfirmationSheet.swift    # high-risk tool gate
│   │   ├── DegradedStatusPanel.swift  # connection lost / stub-mode banner
│   │   ├── OnboardingFlow.swift       # first-run device tour
│   │   ├── SettingsPanel.swift
│   │   ├── ToastStack.swift
│   │   └── PanelTheme.swift           # colors, typography
│   ├── Input/
│   │   ├── TapInput.swift             # tap-on-component hit-test
│   │   ├── PinchInput.swift           # pinch-to-zoom panel
│   │   ├── VoiceCommandRegistry.swift # local intent → AgentEvent
│   │   └── InputAction.swift          # unified action enum
│   ├── State/
│   │   ├── SessionViewModel.swift     # @Observable, top-level state
│   │   ├── DetectionStore.swift       # detections + tracker outputs
│   │   ├── ChatStore.swift            # expert chat history
│   │   ├── ReplayBuffer.swift         # 30s rolling frames + events
│   │   └── ConfigStore.swift          # ORCHESTRATOR_URL, AUTH_TOKEN
│   └── Dev/
│       └── SpikeView.swift            # day-0 canary; ARKit + WS hello + outline test
└── ForgeTests/                        # not used in Phase 1
```

---

## Phase plan

This project is built in **eight phases**, mirroring the Quest project's phase structure.

| Phase | Scope | Workers |
|---|---|---|
| 0 | Xcode project bootstrap + frozen `AgentProto.swift` + `Info.plist` + `Package.swift` | lead |
| 1 | Module scaffolding — six parallel subagents, one per directory below | 6 parallel |
| 2 | Integration — `ForgeApp` + `ForgeRealityView` + `SessionViewModel` wired end-to-end | lead |
| 3 | Day-0 spike (`SpikeView`) — verifies ARKit session starts, WS connects, one outline renders | lead |
| 4 | Vision pipeline — on-device segmenter + tracker + polygon overlay | 1 agent |
| 5 | Expert chat panel + tap-to-focus interaction | 1 agent |
| 6 | UX polish — onboarding, settings, confirmation flow, degraded states | 1 agent |
| 7 | README + zero-TODO sweep + final smoke build | lead |

Phases 4/5/6 may overlap with Phase 1's later subagents if integration is stable.

---

## Phase 1 — module specs

Each subagent receives one block below. The block lists files to create, the public surface, allowed imports, and verification criteria. **Subagents may not edit files outside their assigned directory.**

### Task A — Net/

**Files to create**

- `Forge/Net/OrchestratorSocket.swift`
- `Forge/Net/BackoffPolicy.swift`

**Purpose.** Maintain a single WSS connection to the orchestrator. Encode outgoing `AgentEvent`s, binary `FrameChunk`s, and binary `AudioInChunk`s; decode incoming `AgentEvent`s. Reconnect with exponential backoff on transport errors. Surface connection state via `AsyncSequence`.

**Public API**

```swift
actor OrchestratorSocket {
    init(url: URL, authToken: String, sessionId: String)
    func start() async
    func stop() async
    func send(_ event: AgentEvent) async throws
    func sendFrame(_ chunk: FrameChunk) async throws
    func sendAudio(_ chunk: AudioInChunk) async throws
    var events: AsyncStream<AgentEvent> { get }
    var state: AsyncStream<ConnectionState> { get }
}

enum ConnectionState: Equatable { case connecting, open, degraded(reason: String), closed }
```

**Wire format.** JSON envelope on text frames; binary on binary frames. The first 4 bytes of a binary frame are a magic tag (`"FRAM"` = JPEG, `"AUDI"` = PCM); next 16 bytes are header (width:Int32, height:Int32, timestamp:Int64); remainder is payload. Same as the Quest client.

**Allowed imports**

- `Foundation`
- Shared types: `AgentEvent`, `FrameChunk`, `AudioInChunk`, `Risk`

**Verification**

- Connect to a `wscat` echo server with `start()`; send an `AgentEvent.hello`; receive it back via `events`.
- Disconnect mid-stream; `state` should emit `.degraded(...)` then `.open` after reconnect.

---

### Task B — Camera/

**Files to create**

- `Forge/Camera/ARKitSession.swift`
- `Forge/Camera/FrameEncoder.swift`
- `Forge/Camera/IntrinsicsExtractor.swift`

**Purpose.** Own the `ARSession`. Configure it for world tracking + scene reconstruction (LiDAR) + scene depth. Surface frames as an `AsyncSequence` of `ARFrame` wrappers. Encode the latest frame as JPEG on demand.

**Public API**

```swift
actor ARKitSession {
    init()
    func start() async throws
    func stop() async
    var frames: AsyncStream<ARFrameSample> { get }   // ~60 fps; downstream samples
    var intrinsics: CameraIntrinsics? { get async }  // available after first frame
    func captureLatestJPEG(quality: CGFloat) async -> FrameChunk?
}

struct ARFrameSample {
    let pixelBuffer: CVPixelBuffer    // BGRA, native camera resolution
    let cameraTransform: simd_float4x4
    let intrinsics: CameraIntrinsics
    let sceneDepth: ARDepthData?      // 256x192 Float32, nil on non-LiDAR
    let timestampNs: Int64
}

enum FrameEncoder {
    static func encodeJPEG(_ buf: CVPixelBuffer, quality: CGFloat) -> Data
}
```

**ARSession configuration.**

```swift
let config = ARWorldTrackingConfiguration()
config.planeDetection = [.horizontal, .vertical]
if ARWorldTrackingConfiguration.supportsSceneReconstruction(.meshWithClassification) {
    config.sceneReconstruction = .meshWithClassification
}
if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) {
    config.frameSemantics.insert(.sceneDepth)
}
config.environmentTexturing = .none           // we don't need IBL
config.isAutoFocusEnabled = true
session.run(config, options: [.resetTracking, .removeExistingAnchors])
```

**Allowed imports**

- `ARKit`, `AVFoundation`, `CoreImage`, `Foundation`
- Shared types: `CameraIntrinsics`, `FrameChunk`

**Verification**

- On a LiDAR-equipped device, `frames` emits ≥30 samples/sec with non-nil `sceneDepth`.
- `intrinsics` matches `ARFrame.camera.intrinsics` rounded to integer image-size.

---

### Task C — Audio/

**Files to create**

- `Forge/Audio/MicCapture.swift`
- `Forge/Audio/SpeakerPlayer.swift`
- `Forge/Audio/VoiceIntent.swift`

**Purpose.** Mirror the Quest client's audio pipeline. Capture 16 kHz mono PCM 16-bit from the mic; play 24 kHz PCM 16-bit from Gemini (the orchestrator re-samples).

**Public API**

```swift
actor MicCapture {
    init()
    func start() async throws
    func stop() async
    var chunks: AsyncStream<AudioInChunk> { get }   // 20 ms per chunk
}

actor SpeakerPlayer {
    init()
    func start() async throws
    func stop() async
    func enqueue(_ pcmBase64: String) async         // base64 24 kHz mono LE16
}

actor VoiceIntent {
    init()
    var transcripts: AsyncStream<String> { get }    // local SFSpeechRecognizer; coarse
    func setPushToTalk(_ on: Bool) async
}
```

**Allowed imports**

- `AVFoundation`, `Speech`, `Foundation`
- Shared types: `AudioInChunk`

**Verification**

- `MicCapture.chunks` yields one chunk per 20 ms; each chunk's `pcm.count == 640` bytes (16 kHz × 0.02 s × 2 B).
- `SpeakerPlayer.enqueue` plays a known 1-second tone audibly.

---

### Task D — Spatial/

**Files to create**

- `Forge/Spatial/AnchorRegistry.swift`
- `Forge/Spatial/Raycaster.swift`
- `Forge/Spatial/SceneMeshQuery.swift`
- `Forge/Spatial/PoseProvider.swift`

**Purpose.** Convert image-space detections into world-space anchors. Provide stable IDs across frames. Answer "what world point does pixel (u,v) hit on the scene mesh?"

**Public API**

```swift
actor AnchorRegistry {
    init(session: ARSession)
    func registerOrUpdate(id: String, worldPosition: SIMD3<Float>) async -> ARAnchor
    func remove(id: String) async
    func anchor(forId id: String) async -> ARAnchor?
    var allIds: [String] { get async }
}

enum Raycaster {
    static func pixelToCameraRay(
        pxX: Float, pxY: Float, intrinsics: CameraIntrinsics
    ) -> (origin: SIMD3<Float>, direction: SIMD3<Float>)

    static func cameraRayToWorld(
        rayOriginCam: SIMD3<Float>, rayDirCam: SIMD3<Float>,
        cameraTransform: simd_float4x4
    ) -> (origin: SIMD3<Float>, direction: SIMD3<Float>)
}

actor SceneMeshQuery {
    init(session: ARSession)
    /// Returns the closest hit against the LiDAR scene mesh, or nil.
    func hit(rayOrigin: SIMD3<Float>, rayDir: SIMD3<Float>) async -> SIMD3<Float>?
    /// Convenience: full pipeline image-pixel → world point.
    func worldPoint(forPixel px: SIMD2<Float>, frame: ARFrameSample) async -> SIMD3<Float>?
}

actor PoseProvider {
    init(session: ARSession)
    var poses: AsyncStream<Pose6dof> { get }    // 60 Hz
}
```

**Implementation notes.**

- Prefer ARKit's built-in `ARFrame.raycast(_ query:)` over hand-rolled mesh intersection — pass `target: .existingPlaneGeometry` for plane hits or `target: .estimatedPlane` for unbounded surfaces. Use `SceneMeshQuery` as a fallback only when the raycast misses (LiDAR mesh hit-test via `ARMeshAnchor.geometry` BVH).
- `AnchorRegistry` is the source of truth for "where is component U1 in world space?" across the session.

**Allowed imports**

- `ARKit`, `simd`, `Foundation`
- Shared types: `CameraIntrinsics`, `Pose6dof`

**Verification**

- For a stationary mounted phone, `worldPoint(forPixel:)` returns a stable point (≤2 cm jitter) across 60 frames for the same pixel.
- `AnchorRegistry.registerOrUpdate` returns the same `ARAnchor.identifier` when called twice with the same id.

---

### Task E — Vision/

**Files to create**

- `Forge/Vision/Segmenter.swift`
- `Forge/Vision/Tracker.swift`
- `Forge/Vision/MaskPolygonizer.swift`

**Purpose.** On-device fallback / between-Gemini-refreshes segmentation. The orchestrator's `look_at_bench` returns authoritative segmentation; this module produces interim masks so outlines don't flicker.

**Public API**

```swift
actor Segmenter {
    init()
    /// One-shot: returns instance masks for the foreground objects in the frame.
    func segment(_ buf: CVPixelBuffer) async throws -> [SegmentedInstance]
}

struct SegmentedInstance {
    let id: Int                 // local-only; do not cross-reference with DetectedComponent.id
    let mask: CVPixelBuffer     // 1-channel uint8, 0 or 255
    let boundingBox: CGRect     // normalized 0..1
    let confidence: Float
}

actor Tracker {
    init()
    /// Warp the last known polygons by the optical flow between two frames.
    func warp(
        polygons: [String: [SIMD2<Float>]],
        from previous: CVPixelBuffer,
        to current: CVPixelBuffer
    ) async -> [String: [SIMD2<Float>]]
}

enum MaskPolygonizer {
    /// Marching-squares contour; downsample to ≤32 vertices for wire efficiency.
    static func polygon(from mask: CVPixelBuffer, maxVertices: Int) -> [SIMD2<Float>]
}
```

**Implementation notes.**

- `Segmenter` uses `VNGenerateForegroundInstanceMaskRequest` first (iOS 17+, free, on-Apple-Neural-Engine). If a Core ML model is bundled (`PCBComponentSeg.mlmodelc`), prefer it via `VNCoreMLRequest`.
- `Tracker` is intentionally cheap — Lucas-Kanade pyramid via `vImage` or `cv2` (no OpenCV dep; use Vision's `VNTranslationalImageRegistrationRequest` for a frame-pair affine, then apply to each polygon vertex). Good enough for ≤1 s between Gemini refreshes.

**Allowed imports**

- `Vision`, `CoreML`, `CoreImage`, `Accelerate`, `Foundation`
- Shared types: `Bbox2D`

**Verification**

- On a still-life test frame, `Segmenter.segment` returns ≥1 instance with confidence > 0.7.
- `Tracker.warp` returns polygons whose centroids drift < 5 px on two consecutive frames from a hand-held capture.

---

### Task F — Scene/

**Files to create**

- `Forge/Scene/ForgeRealityView.swift`
- `Forge/Scene/ComponentOutline.swift`
- `Forge/Scene/ComponentLabel.swift`
- `Forge/Scene/ComponentDetailCard.swift`
- `Forge/Scene/ExpertChatPanel.swift`
- `Forge/Scene/HudOverlay.swift`
- `Forge/Scene/ConfirmationSheet.swift`
- `Forge/Scene/DegradedStatusPanel.swift`
- `Forge/Scene/OnboardingFlow.swift`
- `Forge/Scene/SettingsPanel.swift`
- `Forge/Scene/ToastStack.swift`
- `Forge/Scene/PanelTheme.swift`

**Purpose.** Render the AR view plus all SwiftUI chrome. Drive a `RealityView` (iOS 18+) or `ARView` (iOS 17 fallback). Mount a `ModelEntity` outline per detection; attach a SwiftUI label per outline using RealityKit's `Attachment` API.

**Public API**

```swift
struct ForgeRealityView: View {
    @Environment(SessionViewModel.self) private var vm
    var body: some View { /* RealityView + SwiftUI overlay */ }
}

@MainActor final class ComponentOutline {
    init(component: DetectedComponent, polygon: [SIMD3<Float>])
    var entity: ModelEntity { get }
    func update(polygon: [SIMD3<Float>], pose: Pose6dof?) async
    func setHighlighted(_ on: Bool)
}

struct ComponentLabel: View {
    let component: DetectedComponent
    let isFocused: Bool
    let onTap: () -> Void
    var body: some View { /* compact label, expands on focus */ }
}

struct ExpertChatPanel: View {
    let chat: ChatStore
    let focusedComponentId: String?
    var body: some View { /* Discord-style multi-thread */ }
}
```

**Implementation notes.**

- **Outline mesh.** Build a closed line-strip from the world-space polygon using `MeshResource.generateLineStrip(...)` (iOS 17 has `generatePath`; iOS 18 has `LowLevelMesh` for sub-frame updates). Color by component category: ICs orange, passives cyan, connectors magenta.
- **Label leader.** Each outline owns a child `ModelEntity` for the leader line (a thin cylinder from the polygon centroid to the floating label anchor); the `Attachment` mounts the SwiftUI `ComponentLabel` on a billboarded anchor 8 cm above the centroid.
- **Chat panel.** Right-side SwiftUI panel; one thread per detected component plus a global thread. Each thread is a `ScrollViewReader`-driven list of agent messages. Tapping a component scrolls to its thread.
- **Theme.** `PanelTheme` is the single source of truth for colors, font sizes, and depth/opacity behavior. Match `forge_quest/UX_DESIGN.md` color tokens where they apply.

**Allowed imports**

- `RealityKit`, `ARKit`, `SwiftUI`, `Foundation`
- Shared types: `DetectedComponent`, `Bbox2D`, `Pose6dof`, `CameraIntrinsics`, `AgentEvent`, `Risk`
- Cross-module: `SessionViewModel`, `ChatStore`, `DetectionStore` (read-only — observe, don't mutate)

**Verification**

- Mock 3 `DetectedComponent`s with fixed world polygons → `ForgeRealityView` shows three labeled outlines.
- Tapping an outline scrolls `ExpertChatPanel` to the matching thread.

---

### Task G — Input/

**Files to create**

- `Forge/Input/TapInput.swift`
- `Forge/Input/PinchInput.swift`
- `Forge/Input/VoiceCommandRegistry.swift`
- `Forge/Input/InputAction.swift`

**Purpose.** Translate raw gestures and local voice intents into a single `InputAction` enum that `SessionViewModel` consumes. Keep gesture handlers thin.

**Public API**

```swift
enum InputAction: Equatable {
    case tapComponent(id: String)
    case longPressAnchorReset(worldPoint: SIMD3<Float>)
    case pinchScalePanel(delta: Float)
    case voiceCommand(intent: VoiceIntentKind, rawText: String)
    case confirmationAccepted(callId: String)
    case confirmationRejected(callId: String)
}

enum VoiceIntentKind: String, Equatable {
    case lookAtBench, focusComponent, dismissPanel, pauseSession, resumeSession, capture
}

struct TapInput: ViewModifier { /* attaches to ForgeRealityView */ }
struct PinchInput: ViewModifier { /* attaches to panels */ }

actor VoiceCommandRegistry {
    init()
    var actions: AsyncStream<InputAction> { get }
    func feed(_ transcript: String)
}
```

**Allowed imports**

- `SwiftUI`, `ARKit`, `Foundation`
- Shared types: `Risk`

**Verification**

- Tap on a mock outline in a unit-test harness → `InputAction.tapComponent(id:)` is emitted.
- `VoiceCommandRegistry.feed("forge look at my bench")` → emits `.voiceCommand(.lookAtBench, ...)`.

---

### Task H — State/

**Files to create**

- `Forge/State/SessionViewModel.swift`
- `Forge/State/DetectionStore.swift`
- `Forge/State/ChatStore.swift`
- `Forge/State/ReplayBuffer.swift`
- `Forge/State/ConfigStore.swift`

**Purpose.** Top-level reactive state. `SessionViewModel` is the wiring point; it instantiates Net, Camera, Audio, Vision, Spatial, owns the stores, and routes events.

**Public API**

```swift
@Observable @MainActor
final class SessionViewModel {
    init(config: ConfigStore)
    func start() async
    func stop() async

    var connection: ConnectionState
    let detections: DetectionStore
    let chat: ChatStore
    let replay: ReplayBuffer
    var hudStatus: HudStatus
}

@Observable @MainActor
final class DetectionStore {
    var components: [DetectedComponent]
    var worldPolygons: [String: [SIMD3<Float>]]    // id → world-space contour
    var focusedId: String?
    func upsert(_ result: LookAtBenchResult, worldFor: (Bbox2D, [SIMD2<Float>]?) -> [SIMD3<Float>]?)
}

@Observable @MainActor
final class ChatStore {
    var threads: [String: [ChatMessage]]    // componentId or "" for global
    func append(componentId: String?, message: ChatMessage)
}

struct ChatMessage: Identifiable, Equatable {
    let id: UUID
    let author: ChatAuthor       // .user, .agent(name:), .system
    let text: String
    let ts: Int64
}

enum ChatAuthor: Equatable {
    case user, system
    case agent(name: String)     // "signal-integrity", "thermal", "datasheet", ...
}

actor ReplayBuffer {
    init(durationSec: Int = 30)
    func record(event: AgentEvent) async
    func record(frame: FrameChunk) async
    func snapshot() async -> ReplaySnapshot
}

struct ConfigStore {
    var orchestratorURL: URL
    var authToken: String
    static func load() -> ConfigStore   // env / UserDefaults / build setting
}

struct HudStatus: Equatable {
    var fps: Int
    var sessionId: String
    var stubModes: [String]      // which orchestrator adapters are stubbed
}
```

**Allowed imports**

- `Foundation`, `Observation`, `Combine` *(only for legacy bridging)*
- Cross-module: every other Forge module (this is the wiring layer)
- Shared types: all

**Verification**

- `SessionViewModel().start()` connects to a local fake orchestrator, populates `detections.components` after one `look_at_bench` response, and exposes a non-nil `hudStatus.sessionId`.
- `chat.append(componentId: "U1", ...)` updates `chat.threads["U1"]` reactively for SwiftUI consumers.

---

## Phase 2 — Integration (lead)

**Files**

- `Forge/ForgeApp.swift`
- `Forge/App/PermissionGate.swift`
- `Forge/App/RootView.swift`

**Behavior**

1. On app launch, `PermissionGate` requests camera + microphone + ARKit. If denied, show a recovery screen with a deep link to Settings.
2. On grant, `RootView` instantiates a `SessionViewModel` and pushes `ForgeRealityView`.
3. Background lifecycle: pause `ARKitSession` and `OrchestratorSocket` on `.background`; resume on `.active`.
4. Handle universal-link invocations like `forge://session/<id>` (joins an existing orchestrator session).

---

## Phase 3 — Day-0 spike

`Forge/Dev/SpikeView.swift`. A standalone SwiftUI view, accessible via a hidden `#if DEBUG` menu, that does *only*:

1. Start an `ARKitSession`. Print intrinsics and first 5 frame timestamps to console.
2. Connect to `ORCHESTRATOR_URL` from `ConfigStore`. Send `AgentEvent.hello`. Print the round-trip latency.
3. Place one bright-green debug outline as a static `ModelEntity` at the world point hit by a raycast through the screen center. If a hit is found, print "It works."

This validates the three load-bearing assumptions:

- ARKit world tracking + LiDAR mesh hit-test produce sensible world points.
- The orchestrator WS handshake works on the device's network.
- RealityKit can mount a custom mesh entity that stays world-locked when the phone moves.

If the spike runs successfully on first hardware contact, Phases 4+ can proceed in parallel.

---

## Configuration

Build settings (set via `xcconfig` or build args; surfaced via `ConfigStore`):

| Setting | Default | Notes |
|---|---|---|
| `ORCHESTRATOR_URL` | `ws://192.168.1.50:8080/v1/session` | LAN IP of the Mac running the orchestrator |
| `AUTH_TOKEN` | `forge-dev-shared-secret` | matches orchestrator's `DEV_TOKEN` |
| `ENABLE_ON_DEVICE_SEG` | `true` | gates the Vision/ pipeline |
| `GEMINI_REFRESH_SEC` | `3.0` | how often to ship a frame to the orchestrator |
| `LOG_LEVEL` | `info` | os_log subsystem `ai.forge.ios` |

At runtime these may be overridden via the SettingsPanel (writes to `UserDefaults`, suite `ai.forge.ios`).

---

## Stub mode

Mirrors the orchestrator's stub-mode pattern. When the orchestrator is unreachable, the app:

1. Holds `connection = .degraded(reason: "orchestrator unreachable")`.
2. Synthesizes deterministic fake `LookAtBenchResult`s every 2 seconds — three components labeled "U1 (stub)", "R3 (stub)", "C12 (stub)" at fixed pixel locations.
3. Shows the `DegradedStatusPanel` banner.

This keeps the demo loop running even if the orchestrator dies mid-presentation.

---

## Non-goals

- **Cross-platform.** This is an iOS-only client. Android companion = `forge_quest/` or a future `forge_android/`. No Flutter, no React Native.
- **Multi-user sync.** The orchestrator may multiplex sessions but the iOS client only renders its own session's detections.
- **On-device LLM.** All semantic reasoning is in Gemini. The Vision/ module is segmentation only — no part-number ID, no datasheet matching, no agent decisions.
- **Object scanning / model capture.** ARKit's `ARObjectAnchor` is out of scope for v1 (would let us pose-track a known PCB, but is a Phase 2 enhancement).
- **iPadOS-specific UX.** App runs on iPad but the chrome is iPhone-first; no split-view or stage manager affordances.

---

## What "done" looks like for Phase 1

```bash
# 1. Build clean
xcodebuild -project Forge.xcodeproj -scheme Forge -destination 'platform=iOS,name=...' \
    -derivedDataPath build clean build

# 2. No TODO/FIXME/XXX
grep -rn 'TODO\|FIXME\|XXX' Forge   # expect: no matches

# 3. Each module compiles in isolation
swift build --target Net
swift build --target Vision
# ...etc per Swift package boundary (if we adopt SPM module split in Phase 7)

# 4. Day-0 spike passes on a real iPhone Pro:
#    - "It works." prints to console
#    - one green outline is visible and world-locked
#    - WS hello roundtrip < 200 ms on LAN
```

Phases 4+ build on top — those are tracked in a separate `UX_DESIGN.md` (to be authored alongside Phase 1).

---

## Open questions to resolve before Phase 1 starts

1. **LiDAR-only?** v1 assumes a Pro device. Do we ship a non-LiDAR fallback (pure Vision depth from `VNGenerateForegroundInstanceMaskRequest` + monocular depth)? Recommend: **no for hackathon**, yes post-event.
2. **RealityView vs ARView.** iOS 18 ships `RealityView`, which is the future. iOS 17 only has the older `ARView` via `UIViewRepresentable`. Recommend: **prefer `RealityView` with `#available(iOS 18.0, *)`**; fall back to `ARView` otherwise. Single codepath for entity ownership.
3. **Core ML model for PCB segmentation.** Do we bundle a Roboflow-trained YOLOv11-seg model (`PCBComponentSeg.mlmodelc`, ~40 MB), or rely entirely on Gemini + `VNGenerateForegroundInstanceMaskRequest`? Recommend: **demo without the bundled model**, mention as Phase 2.
4. **Auth.** Firebase ID token verification is in the orchestrator; for the demo we use the shared `DEV_TOKEN`. Production iOS would need Sign-in-with-Apple → Firebase token. Recommend: **shared secret for hackathon, gate behind `ENABLE_FIREBASE_AUTH` flag.**
5. **Expert-agent chat backend.** Where does the chat history live — orchestrator (server-authoritative), or ChatStore (client-authoritative)? Recommend: **server-authoritative**, with `ChatStore` as a thin local cache that consumes `AgentEvent.toolResult` and a new `AgentEvent.chatMessage` envelope. Requires a wire-protocol bump (Phase 0.5).

---

## Appendix — Mapping iOS modules to existing Quest modules

For reviewers familiar with `forge_quest/`:

| Quest (`forge_quest/app/.../`) | iOS (`forge_ios/Forge/`) | Notes |
|---|---|---|
| `MainActivity.kt` | `ForgeApp.swift` + `RootView.swift` | SwiftUI App lifecycle replaces `AppSystemActivity`. |
| `proto/AgentProto.kt` | `Proto/AgentProto.swift` | One-to-one schema mirror. |
| `net/OrchestratorSocket.kt` | `Net/OrchestratorSocket.swift` | `URLSessionWebSocketTask` instead of OkHttp. |
| `camera/PassthroughCapture.kt` | `Camera/ARKitSession.swift` | ARKit gives us camera + depth + pose in one stream — simpler than Camera2 + manual intrinsics. |
| `camera/IntrinsicsLoader.kt` | `Camera/IntrinsicsExtractor.swift` | One line: `frame.camera.intrinsics`. |
| `spatial/Raycaster.kt` | `Spatial/Raycaster.swift` + `Spatial/SceneMeshQuery.swift` | Pixel→ray math same; mesh hit-test gets a real LiDAR mesh instead of MRUK approximation. |
| `spatial/AnchorRegistry.kt` | `Spatial/AnchorRegistry.swift` | `ARAnchor` lifecycle. |
| `spatial/PoseProvider.kt` | `Spatial/PoseProvider.swift` | `ARFrame.camera.transform`. |
| `scene/ForgeScene.kt` | `Scene/ForgeRealityView.swift` | `RealityView` replaces `PanelRegistration` DSL. |
| `scene/ComponentLabel.kt` | `Scene/ComponentLabel.swift` | SwiftUI view mounted via RealityKit `Attachment`. |
| `scene/HudPanel.kt` | `Scene/HudOverlay.swift` | Pure SwiftUI, no Spatial SDK panel needed. |
| `scene/ConfirmationPanel.kt` | `Scene/ConfirmationSheet.swift` | Native iOS sheet. |
| `audio/MicCapture.kt` (Camera2/AudioRecord) | `Audio/MicCapture.swift` (AVAudioEngine) | Same 16 kHz PCM contract. |
| `audio/SpeakerPlayer.kt` | `Audio/SpeakerPlayer.swift` | Same 24 kHz PCM contract. |
| `input/ControllerInput.kt` + `HandInput.kt` | `Input/TapInput.swift` + `Input/PinchInput.swift` | Touch instead of controllers/hands. |
| `input/VoiceCommandRegistry.kt` | `Input/VoiceCommandRegistry.swift` | `SFSpeechRecognizer` for local intents. |
| `state/SessionViewModel.kt` | `State/SessionViewModel.swift` | `@Observable` instead of `ViewModel`. |
| `dev/SpikeActivity.kt` | `Dev/SpikeView.swift` | Hidden debug entry point. |

This mapping is intentional — reviewers, future maintainers, and you-three-weeks-from-now should be able to read either codebase by understanding the other.
