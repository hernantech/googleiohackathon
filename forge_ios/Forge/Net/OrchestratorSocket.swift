import Foundation

// MARK: - Connection state

enum ConnectionState: Equatable {
    case connecting
    case open
    case degraded(reason: String)
    case closed
}

// MARK: - OrchestratorSocket

actor OrchestratorSocket {

    // MARK: Public streams

    let events: AsyncStream<AgentEvent>
    let state: AsyncStream<ConnectionState>

    // MARK: Private

    private let url: URL
    private let authToken: String
    private let sessionId: String

    private let eventsContinuation: AsyncStream<AgentEvent>.Continuation
    private let stateContinuation: AsyncStream<ConnectionState>.Continuation

    private var task: URLSessionWebSocketTask?
    private var stopped = false
    private var fatalClose = false           // auth_failed / protocol_mismatch — do not reconnect
    private var lastCheckpointId: String?    // drives replayFrom on reconnect (specs/04 §6)
    private var lastActivity = Date()         // missed-ping watchdog

    // MARK: Init

    init(url: URL, authToken: String, sessionId: String) {
        self.url = url
        self.authToken = authToken
        self.sessionId = sessionId

        let (evStream, evCont) = AsyncStream<AgentEvent>.makeStream()
        let (stStream, stCont) = AsyncStream<ConnectionState>.makeStream()
        self.events = evStream
        self.state = stStream
        self.eventsContinuation = evCont
        self.stateContinuation = stCont
    }

    // MARK: Public API

    func start() async {
        stopped = false
        await connect()
    }

    func stop() async {
        stopped = true
        if let t = task {
            // Best-effort goodbye before closing.
            let goodbye = AgentEvent.goodbye(reason: "client stop")
            if let data = try? goodbye.jsonData() {
                try? await t.send(.string(String(decoding: data, as: UTF8.self)))
            }
            t.cancel(with: .normalClosure, reason: nil)
            task = nil
        }
        stateContinuation.yield(.closed)
        stateContinuation.finish()
        eventsContinuation.finish()
    }

    func send(_ event: AgentEvent) async throws {
        guard let t = task else { throw SocketError.notConnected }
        let data = try event.jsonData()
        try await t.send(.string(String(decoding: data, as: UTF8.self)))
    }

    func sendFrame(_ chunk: FrameChunk) async throws {
        guard let t = task else { throw SocketError.notConnected }
        let frame = buildBinaryFrame(
            magic: "FRAM",
            width: Int32(chunk.widthPx),
            height: Int32(chunk.heightPx),
            timestamp: chunk.timestampNs,
            payload: chunk.jpegBytes
        )
        try await t.send(.data(frame))
    }

    func sendAudio(_ chunk: AudioInChunk) async throws {
        guard let t = task else { throw SocketError.notConnected }
        let frame = buildBinaryFrame(
            magic: "AUDI",
            width: 0,
            height: 0,
            timestamp: chunk.timestampNs,
            payload: chunk.pcm
        )
        try await t.send(.data(frame))
    }

    // MARK: Connection lifecycle

    private func connect() async {
        var backoff = BackoffPolicy()

        while !stopped && !fatalClose {
            stateContinuation.yield(.connecting)
            Log.net.info("connecting to \(self.url.absoluteString, privacy: .public)")

            // v2 auth rides the WS subprotocol, not an Authorization header (specs/00 §8, 04 §1).
            // Offer the bare token as a second subprotocol entry so the server's
            // _auth_subprotocol matcher (exact equality against ALLOWED_DEV_TOKENS)
            // can find it.  "forge.chat.v2" stays first as the application-level
            // protocol identifier (specs/00 §8).
            let session = URLSession(configuration: .default)
            let wsTask = session.webSocketTask(
                with: connectURL(replayFrom: lastCheckpointId),
                protocols: ["forge.chat.v2", authToken]
            )
            task = wsTask
            wsTask.resume()

            // Don't claim .open on resume() — it doesn't confirm the socket
            // connected. We announce .open on the first frame received (the
            // orchestrator emits ChannelList on connect, specs/04 §6); until then
            // the UI stays .connecting and SessionViewModel runs stub mode.
            let sid = sessionId
            Task { [weak self] in
                // Hello MUST carry protocolVersion or the orchestrator rejects
                // with Goodbye("protocol_mismatch") (specs/00 §7).
                try? await self?.send(.hello(client: "ios", sessionId: sid, protocolVersion: "2.0"))
            }
            lastActivity = Date()

            // Missed-ping watchdog: 2 missed 20s pings ⇒ treat dead (specs/04 §5).
            let watchdog = Task { [weak self] in
                while !Task.isCancelled {
                    try? await Task.sleep(nanoseconds: 5_000_000_000)
                    guard let self else { return }
                    if await self.activityIsStale() { await self.dropDeadConnection(); return }
                }
            }

            let didConnect = await receiveLoop(task: wsTask)
            watchdog.cancel()

            if stopped || fatalClose { break }

            if didConnect { backoff.reset() }   // only reset after a real connection
            let delay = backoff.next()
            Log.net.notice("connection lost — retrying in \(Int(delay))s")
            stateContinuation.yield(.degraded(reason: "orchestrator unreachable — retrying in \(Int(delay))s"))
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
        }

        if fatalClose {
            stateContinuation.yield(.closed)
            stateContinuation.finish()
            eventsContinuation.finish()
        }
    }

    /// `wss://…/v2/chat?sessionId=…&client=ios[&replayFrom=…]` (specs/04 §1).
    private func connectURL(replayFrom: String?) -> URL {
        guard var comps = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return url }
        var items = [URLQueryItem(name: "sessionId", value: sessionId),
                     URLQueryItem(name: "client", value: "ios")]
        if let replayFrom { items.append(URLQueryItem(name: "replayFrom", value: replayFrom)) }
        comps.queryItems = (comps.queryItems ?? []) + items
        return comps.url ?? url
    }

    private func activityIsStale() -> Bool { Date().timeIntervalSince(lastActivity) > 45 }
    private func dropDeadConnection() { task?.cancel(with: .goingAway, reason: nil) }

    /// Drains the receive loop until the task fails or `stopped` is set.
    /// Returns true if at least one frame was received (the socket really
    /// opened), so the caller knows whether to reset backoff.
    @discardableResult
    private func receiveLoop(task wsTask: URLSessionWebSocketTask) async -> Bool {
        var didOpen = false
        while !stopped {
            do {
                let message = try await wsTask.receive()
                if !didOpen {
                    didOpen = true
                    stateContinuation.yield(.open)
                    Log.net.info("socket open (first frame received)")
                }
                lastActivity = Date()
                switch message {
                case .string(let text):
                    guard let data = text.data(using: .utf8) else { continue }
                    do {
                        let event = try AgentEvent.decode(from: data)
                        await handleIncoming(event)
                    } catch is AgentEventDecodingError {
                        // Unknown kind (forward-compat) — drop silently.
                        continue
                    }
                case .data:
                    // The v2 chat bus carries no inbound binary frames.
                    continue
                @unknown default:
                    continue
                }
            } catch {
                // Any receive error exits the loop so the caller can reconnect.
                return didOpen
            }
        }
        return didOpen
    }

    /// Intercepts transport control events (Ping, fatal errors, replay anchors)
    /// before forwarding everything else to consumers.
    private func handleIncoming(_ event: AgentEvent) async {
        switch event {
        case let .ping(nonce):
            try? await send(.pong(nonce: nonce))
            return                                  // not surfaced to consumers
        case let .checkpointMarker(marker):
            lastCheckpointId = marker.checkpointId
        case let .replayDone(done):
            if let cp = done.checkpointId { lastCheckpointId = cp }
        case let .errorEvent(err):
            Log.net.error("orchestrator error \(err.code, privacy: .public): \(err.message, privacy: .public)")
            if err.isFatal { fatalClose = true }
        case let .goodbye(reason):
            Log.net.notice("goodbye: \(reason, privacy: .public)")
            if reason == "protocol_mismatch" || reason == "auth_failed" { fatalClose = true }
        default:
            break
        }
        eventsContinuation.yield(event)
    }

    // MARK: Binary framing

    /// 20-byte header: 4-byte ASCII magic + Int32LE width + Int32LE height + Int64LE timestamp.
    private func buildBinaryFrame(
        magic: String,
        width: Int32,
        height: Int32,
        timestamp: Int64,
        payload: Data
    ) -> Data {
        var frame = Data(capacity: 20 + payload.count)

        // 4-byte ASCII magic.
        let magicBytes = Array(magic.utf8).prefix(4)
        frame.append(contentsOf: magicBytes)

        // Int32 LE width.
        var w = width.littleEndian
        withUnsafeBytes(of: &w) { frame.append(contentsOf: $0) }

        // Int32 LE height.
        var h = height.littleEndian
        withUnsafeBytes(of: &h) { frame.append(contentsOf: $0) }

        // Int64 LE timestamp.
        var ts = timestamp.littleEndian
        withUnsafeBytes(of: &ts) { frame.append(contentsOf: $0) }

        frame.append(payload)
        return frame
    }
}

// MARK: - SocketError

private enum SocketError: Error {
    case notConnected
}
