import Foundation
import Observation
import ARKit
import simd

// MARK: - HudStatus

struct HudStatus: Equatable {
    var fps: Int
    var sessionId: String
    var stubModes: [String]
}

// Pending high-risk action awaiting approval. Carries the v2 ActionCard +
// invoker identity (specs/00 §2.1, 03 §4) when the orchestrator supplies them.
struct PendingConfirmation: Equatable {
    let callId: String
    let summary: String
    let risk: Risk
    let invokerSmeId: String?
    let actionCard: ActionCard?
}

// MARK: - SessionViewModel

@Observable @MainActor
final class SessionViewModel {

    // MARK: Public state

    var connection: ConnectionState = .closed
    let detections: DetectionStore = DetectionStore()
    let chat: ChatStore = ChatStore()
    let replay: ReplayBuffer = ReplayBuffer()
    var hudStatus: HudStatus = HudStatus(fps: 0, sessionId: "", stubModes: [])
    private(set) var arSession: ARSession? = nil
    var pendingConfirmation: PendingConfirmation? = nil
    var safetyInterrupt: SafetyInterrupt? = nil    // WARN banner / HALT takeover (specs/03 §5)
    var lastError: ErrorEvent? = nil               // last fatal/transport error (specs/04 §8)
    var isReplaying: Bool = false                  // suppress notifications during replay (specs/04 §6)

    static let defaultChannels: [ChannelInfo] = [
        ChannelInfo(id: "#live-feed", title: "Live", icon: "🔴", alwaysVisible: true),
        ChannelInfo(id: "#user", title: "You", icon: "🗣️", alwaysVisible: true),
        ChannelInfo(id: "#actions", title: "Actions", icon: "⚡"),
        ChannelInfo(id: "#dissent", title: "Dissent", icon: "⚔️"),
        ChannelInfo(id: "#general", title: "General", icon: "💬", alwaysVisible: true),
    ]

    // MARK: Private — config

    private let config: ConfigStore

    // MARK: Private — modules (instantiated in start())

    private var socket: OrchestratorSocket?
    private var camera: ARKitSession?
    private var mic: MicCapture?
    private var speaker: SpeakerPlayer?
    private var voiceIntent: VoiceIntent?
    private var segmenter: Segmenter?
    private var tracker: Tracker?
    private var anchorRegistry: AnchorRegistry?
    private var sceneMesh: SceneMeshQuery?
    private var poseProvider: PoseProvider?
    private var voiceCommands: VoiceCommandRegistry?

    // MARK: Private — lifecycle

    private var childTasks: [Task<Void, Never>] = []
    private var stubTask: Task<Void, Never>? = nil

    // Session id comes from the hello handshake.
    private var sessionId: String = UUID().uuidString

    // Gemini refresh interval (~3 s).
    private let geminiRefreshNs: UInt64 = 3_000_000_000

    // MARK: Init

    init(config: ConfigStore) {
        self.config = config
    }

    // MARK: - start / stop

    func start() async {
        Log.session.info("session start (config \(self.config.orchestratorURL.absoluteString, privacy: .public))")
        // Build modules.
        let cameraActor = ARKitSession()
        let socketActor = OrchestratorSocket(
            url: config.orchestratorURL,
            authToken: config.authToken,
            sessionId: sessionId
        )
        let micActor = MicCapture()
        let speakerActor = SpeakerPlayer()
        let voiceIntentActor = VoiceIntent()
        let segmenterActor = Segmenter()
        let trackerActor = Tracker()
        let anchorReg = AnchorRegistry(session: cameraActor.session)
        let meshQuery = SceneMeshQuery(session: cameraActor.session)
        let poseProviderActor = PoseProvider(session: cameraActor.session)
        let voiceCmdReg = VoiceCommandRegistry()

        camera = cameraActor
        socket = socketActor
        mic = micActor
        speaker = speakerActor
        voiceIntent = voiceIntentActor
        segmenter = segmenterActor
        tracker = trackerActor
        anchorRegistry = anchorReg
        sceneMesh = meshQuery
        poseProvider = poseProviderActor
        voiceCommands = voiceCmdReg

        arSession = cameraActor.session

        // Seed channels so the chat UI is populated before the orchestrator's
        // ChannelList arrives (and in offline/stub mode it never does).
        if chat.channels.isEmpty { chat.setChannels(SessionViewModel.defaultChannels) }

        // Start modules.
        try? await cameraActor.start()
        await socketActor.start()
        try? await micActor.start()
        try? await speakerActor.start()

        // Socket connection state → update `connection`.
        let t1 = Task { [weak self] in
            guard let self else { return }
            for await state in socketActor.state {
                await self.handleConnectionState(state)
            }
        }

        // Socket events → route.
        let t2 = Task { [weak self] in
            guard let self else { return }
            for await event in socketActor.events {
                await self.handleEvent(event, meshQuery: meshQuery)
            }
        }

        // Mic → socket.
        let t3 = Task { [weak self] in
            guard let self else { return }
            for await chunk in await micActor.chunks {
                guard let socket = self.socket else { continue }
                try? await socket.sendAudio(chunk)
            }
        }

        // Voice transcripts → command registry.
        let t4 = Task { [weak self] in
            guard let self else { return }
            for await transcript in await voiceIntentActor.transcripts {
                await voiceCmdReg.feed(transcript)
            }
        }

        // Command registry actions → send.
        let t5 = Task { [weak self] in
            guard let self else { return }
            for await action in voiceCmdReg.actions {
                self.send(action)
            }
        }

        // Camera frames → throttle to GEMINI_REFRESH → sendFrame.
        let t6 = Task { [weak self] in
            guard let self else { return }
            var lastSentNs: Int64 = 0
            for await frame in await cameraActor.frames {
                let nowNs = frame.timestampNs
                guard nowNs - lastSentNs >= Int64(self.geminiRefreshNs) else { continue }
                lastSentNs = nowNs

                // FPS bookkeeping (rough: frames seen per refresh window).
                let fps = Int(1_000_000_000 / max(1, self.geminiRefreshNs))
                let currentHud = self.hudStatus
                self.hudStatus = HudStatus(fps: fps, sessionId: currentHud.sessionId, stubModes: currentHud.stubModes)

                guard let socket = self.socket, let camera = self.camera else { continue }
                if let chunk = await camera.captureLatestJPEG(quality: 0.7) {
                    await self.replay.record(frame: chunk)
                    try? await socket.sendFrame(chunk)
                }
            }
        }

        childTasks = [t1, t2, t3, t4, t5, t6]
    }

    func stop() async {
        for t in childTasks { t.cancel() }
        childTasks = []
        stubTask?.cancel()
        stubTask = nil

        await socket?.stop()
        await camera?.stop()
        await mic?.stop()
        await speaker?.stop()

        socket = nil
        camera = nil
        mic = nil
        speaker = nil
        voiceIntent = nil
        segmenter = nil
        tracker = nil
        anchorRegistry = nil
        sceneMesh = nil
        poseProvider = nil
        voiceCommands = nil
        arSession = nil
        connection = .closed
    }

    // MARK: - send (fire-and-forget)

    func send(_ action: InputAction) {
        Task { [weak self] in
            await self?.handleAction(action)
        }
    }

    /// Client→server typed chat (specs/04 §7). Optimistically ingested; the
    /// server echo dedups by `messageId`.
    func sendChat(_ text: String, channelId: String? = nil) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        let msg = ChatMessage(channelId: channelId ?? chat.selectedChannelId,
                              authorId: "@user", authorKind: .user, body: trimmed,
                              messageId: UUID().uuidString, ts: nowNs())
        chat.ingest(msg)
        Task { [weak self] in try? await self?.socket?.send(.chatMessage(msg)) }
    }

    /// Mute is a client preference; the server still ships all messages but logs
    /// the Subscribe/Unsubscribe for ranking (specs/04 §7).
    func setMuted(_ channelId: String, _ muted: Bool) {
        if muted { chat.muted.insert(channelId) } else { chat.muted.remove(channelId) }
        Task { [weak self] in
            try? await self?.socket?.send(muted ? .unsubscribe(channelId: channelId) : .subscribe(channelId: channelId))
        }
    }

    /// Dismiss an acknowledged safety interrupt (HALT takeover / WARN banner).
    func dismissSafetyInterrupt() { safetyInterrupt = nil }

    // MARK: - Private event routing

    private func handleConnectionState(_ state: ConnectionState) async {
        Log.session.notice("connection → \(String(describing: state), privacy: .public)")
        connection = state
        switch state {
        case .degraded, .closed:
            enterStubMode()
        case .open:
            exitStubMode()
            let hud = hudStatus
            hudStatus = HudStatus(fps: hud.fps, sessionId: sessionId, stubModes: [])
        default:
            break
        }
    }

    private func handleEvent(_ event: AgentEvent, meshQuery: SceneMeshQuery) async {
        await replay.record(event: event)
        switch event {
        case let .toolResult(_, resultJson, deferred):
            if deferred {
                // Async function-call hedge (specs/00 §3): show a pending state.
                chat.system("Consulting the guild…", channelId: "#actions", ts: nowNs())
                return
            }
            guard let result = LookAtBenchResult.from(resultJson: resultJson) else { return }
            await applyLookAtBenchResult(result, meshQuery: meshQuery)

        case let .transcript(text, partial, ts, speaker, smeId):
            guard !partial else { return }   // only commit final transcripts to chat
            let (channelId, authorId, kind) = transcriptRoute(speaker: speaker, smeId: smeId)
            chat.ingest(ChatMessage(channelId: channelId, authorId: authorId, authorKind: kind,
                                    body: text, messageId: "tr-\(ts)-\(authorId)", ts: ts))

        case let .confirmationRequest(callId, summary, risk, invokerSmeId, actionCardJson):
            pendingConfirmation = PendingConfirmation(
                callId: callId, summary: summary, risk: risk,
                invokerSmeId: invokerSmeId, actionCard: ActionCard.from(json: actionCardJson))

        case .confirmationResponse:
            pendingConfirmation = nil

        case let .audioChunk(pcmBase64, _):
            await speaker?.enqueue(pcmBase64)

        case let .hello(_, sid, _):
            sessionId = sid
            hudStatus = HudStatus(fps: hudStatus.fps, sessionId: sid, stubModes: hudStatus.stubModes)

        case let .chatMessage(msg):
            chat.ingest(msg)

        case let .channelUpdate(update):
            chat.apply(update)

        case let .channelList(list):
            chat.setChannels(list.channels)

        case let .smeResponse(resp):
            let channel = "#\(resp.smeId.replacingOccurrences(of: "@", with: ""))"
            let body = ChatCard.wrapJSON(kind: "SmeResponse", resp) ?? resp.claim
            chat.ingest(ChatMessage(channelId: channel, authorId: resp.smeId, authorKind: .sme,
                                    body: body, bodyContentType: .json,
                                    messageId: "sme-\(resp.callId)-\(resp.smeId)", ts: resp.ts))

        case let .dissentReport(report):
            let body = ChatCard.wrapJSON(kind: "DissentReport", report) ?? report.summary
            chat.ingest(ChatMessage(channelId: "#dissent", authorId: "@system", authorKind: .system,
                                    body: body, bodyContentType: .json,
                                    messageId: "dissent-\(report.callId)-\(report.ts)", ts: report.ts))

        case let .safetyInterrupt(interrupt):
            Log.session.warning("safety interrupt \(String(describing: interrupt.severity), privacy: .public): \(interrupt.reason, privacy: .public)")
            safetyInterrupt = interrupt   // Scene shows WARN banner / HALT takeover

        case let .summonGuild(summon):
            let who = summon.smes.joined(separator: ", ")
            chat.system("Consulting the guild on \(summon.topic)\(who.isEmpty ? "" : " — \(who)")",
                        channelId: "#actions", ts: nowNs())

        case .replayDone:
            isReplaying = false

        case let .backpressureNotice(notice):
            chat.system("Stream degraded — dropped \(notice.dropped) updates.",
                        channelId: "#general", ts: nowNs())

        case let .errorEvent(err):
            lastError = err
            if err.isFatal { connection = .degraded(reason: err.message) }

        case let .channelHint(hint):
            if hint.hint == "focus" { chat.selectedChannelId = hint.channelId }

        case .goodbye, .checkpointMarker, .ping, .pong, .toolCall, .subscribe, .unsubscribe:
            break
        }
    }

    private func transcriptRoute(speaker: Speaker, smeId: String?) -> (channelId: String, authorId: String, kind: AuthorKind) {
        switch speaker {
        case .user: return ("#user", "@user", .user)
        case .live: return ("#live-feed", "@live", .live)
        case .sme:
            let s = smeId ?? "@sme"
            return ("#\(s.replacingOccurrences(of: "@", with: ""))", s, .sme)
        }
    }

    private func nowNs() -> Int64 { Int64(Date().timeIntervalSince1970 * 1_000_000_000) }

    private func applyLookAtBenchResult(_ result: LookAtBenchResult, meshQuery: SceneMeshQuery) async {
        guard let camera = camera else {
            detections.upsert(result) { _, _ in nil }
            return
        }

        // Pre-compute world points for every bbox/mask asynchronously before
        // the synchronous upsert closure is called.
        var worldMap = [Bbox2D: [SIMD3<Float>]]()

        for component in result.components {
            let bbox = component.bbox
            let maskVerts = component.maskPolygon

            if let verts = maskVerts, !verts.isEmpty {
                // Resolve a sample of mask vertices to world space.
                var worldPts = [SIMD3<Float>]()
                for v in verts.prefix(16) {
                    // mask polygon is normalized 0..1; convert to pixel space on the fly
                    // using a reference frame sample if available.
                    if let frame = await camera.intrinsics {
                        let px = SIMD2<Float>(
                            v.x * Float(frame.imageSizePx.x),
                            v.y * Float(frame.imageSizePx.y)
                        )
                        // Reuse the latest ARFrame via a synthetic ARFrameSample.
                        if let sample = await latestFrameSample(),
                           let wp = await meshQuery.worldPoint(forPixel: px, frame: sample) {
                            worldPts.append(wp)
                        }
                    }
                }
                if !worldPts.isEmpty { worldMap[bbox] = worldPts }
            } else {
                // Bbox center + corners fan.
                let pts: [SIMD2<Float>] = [
                    SIMD2(Float(bbox.x1), Float(bbox.y1)),
                    SIMD2(Float(bbox.x2), Float(bbox.y1)),
                    SIMD2(Float(bbox.x2), Float(bbox.y2)),
                    SIMD2(Float(bbox.x1), Float(bbox.y2)),
                    bbox.centerPx,
                ]
                if let sample = await latestFrameSample() {
                    var worldPts = [SIMD3<Float>]()
                    for px in pts {
                        if let wp = await meshQuery.worldPoint(forPixel: px, frame: sample) {
                            worldPts.append(wp)
                        }
                    }
                    if !worldPts.isEmpty { worldMap[bbox] = worldPts }
                }
            }
        }

        // Synchronous upsert — all awaiting is complete.
        detections.upsert(result) { bbox, maskPolygon in
            worldMap[bbox]
        }
    }

    // Reads the latest ARFrameSample by pulling a single value from the stream;
    // returns nil if the camera is not yet running.
    private func latestFrameSample() async -> ARFrameSample? {
        // SceneMeshQuery already holds the ARSession reference; we retrieve the
        // current frame directly from the shared session.
        guard let arSess = arSession,
              let frame = arSess.currentFrame else { return nil }

        // Build a minimal ARFrameSample from the current ARFrame so we can call
        // SceneMeshQuery.worldPoint(forPixel:frame:) which only needs intrinsics
        // and the cameraTransform.
        let intr = IntrinsicsExtractor.extract(from: frame.camera)
        return ARFrameSample(
            pixelBuffer: frame.capturedImage,
            cameraTransform: frame.camera.transform,
            intrinsics: intr,
            sceneDepth: frame.sceneDepth,
            timestampNs: Int64(frame.timestamp * 1_000_000_000)
        )
    }

    private func handleAction(_ action: InputAction) async {
        switch action {
        case let .tapComponent(id: id):
            detections.focusedId = id
            let argsJson = "{\"id\":\"\(id)\"}"
            let event = AgentEvent.toolCall(
                name: "expert_chat.focus",
                argsJson: argsJson,
                callId: UUID().uuidString
            )
            try? await socket?.send(event)

        case .voiceCommand(intent: .lookAtBench, rawText: _):
            let event = AgentEvent.toolCall(
                name: "look_at_bench",
                argsJson: "{}",
                callId: UUID().uuidString
            )
            try? await socket?.send(event)

        case let .confirmationAccepted(callId: callId):
            // The iOS approve button is the "chat" approval path (specs/03 §2).
            try? await socket?.send(.confirmationResponse(callId: callId, approved: true, approverChannel: .chat))
            pendingConfirmation = nil

        case let .confirmationRejected(callId: callId):
            try? await socket?.send(.confirmationResponse(callId: callId, approved: false, approverChannel: .chat))
            pendingConfirmation = nil

        default:
            break
        }
    }

    // MARK: - Stub mode

    private func enterStubMode() {
        guard stubTask == nil else { return }
        Log.session.notice("entering stub mode — synthetic detections every 2s")
        let hud = hudStatus
        hudStatus = HudStatus(fps: hud.fps, sessionId: hud.sessionId, stubModes: ["orchestrator"])

        stubTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                guard !Task.isCancelled else { break }
                await self?.emitStubDetections()
            }
        }
    }

    private func exitStubMode() {
        stubTask?.cancel()
        stubTask = nil
    }

    private func emitStubDetections() async {
        // Fixed normalized pixel locations (image-space 0..1 mapped to 1920×1440 equivalent).
        let imgW: Float = 1920; let imgH: Float = 1440
        let stubComponents: [(id: String, partNumber: String, cx: Float, cy: Float)] = [
            ("U1_stub", "U1 (stub)", 0.25, 0.45),
            ("R3_stub", "R3 (stub)", 0.55, 0.60),
            ("C12_stub", "C12 (stub)", 0.75, 0.35),
        ]

        var components = [DetectedComponent]()
        for spec in stubComponents {
            let hw: Float = 60; let hh: Float = 40
            let bbox = Bbox2D(
                x1: Int32(spec.cx * imgW - hw),
                y1: Int32(spec.cy * imgH - hh),
                x2: Int32(spec.cx * imgW + hw),
                y2: Int32(spec.cy * imgH + hh)
            )
            components.append(DetectedComponent(
                id: spec.id,
                partNumber: spec.partNumber,
                bbox: bbox,
                confidence: 1.0
            ))
        }

        let result = LookAtBenchResult(components: components, frameTimestampNs: nil, sceneSummary: "stub")

        // In stub mode the scene mesh may not have real hits; project a small fan
        // in front of the camera using the last known pose, else use fixed offsets.
        let cameraForward: SIMD3<Float>
        if let arSess = arSession, let frame = arSess.currentFrame {
            // -Z axis of camera in world space.
            let col2 = frame.camera.transform.columns.2
            cameraForward = -SIMD3<Float>(col2.x, col2.y, col2.z)
        } else {
            cameraForward = SIMD3<Float>(0, 0, -1)
        }
        let basePos: SIMD3<Float>
        if let arSess = arSession, let frame = arSess.currentFrame {
            let c3 = frame.camera.transform.columns.3
            basePos = SIMD3<Float>(c3.x, c3.y, c3.z)
        } else {
            basePos = .zero
        }
        let depth: Float = 0.5   // 50 cm in front of camera

        detections.upsert(result) { bbox, _ in
            let center = basePos + cameraForward * depth
            // Tiny quad so outlines are visually distinct.
            let hw2: Float = 0.03; let hh2: Float = 0.02
            return [
                center + SIMD3<Float>(-hw2,  hh2, 0),
                center + SIMD3<Float>( hw2,  hh2, 0),
                center + SIMD3<Float>( hw2, -hh2, 0),
                center + SIMD3<Float>(-hw2, -hh2, 0),
            ]
        }
    }
}
