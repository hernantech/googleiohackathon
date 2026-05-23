import Foundation

// MARK: - LiveSocket
//
// Always-on WebSocket for channel (B): H.264 video + PCM audio streamed to
// Gemini Live via the orchestrator's /v2/live endpoint (specs/00 §4.1).
//
// The chat bus (OrchestratorSocket) and the live socket are SEPARATE connections:
//   • OrchestratorSocket → WSS /v2/chat  (JSON only; AgentEvents)
//   • LiveSocket         → WSS /v2/live  (binary; H.264 + PCM in Gemini Live framing)
//
// The orchestrator passes the H.264 bytes straight through to Gemini Live
// without decode/re-encode (specs/00 §4.1, HANDOFF §2.D).  We send raw
// binary frames; the server never replies with video back on this socket.
//
// Auth: `Sec-WebSocket-Protocol: forge.live.v2, bearer.<token>` (specs/00 §8).

actor LiveSocket {

    // MARK: Connection state (mirrors OrchestratorSocket)
    let state: AsyncStream<ConnectionState>
    private let stateContinuation: AsyncStream<ConnectionState>.Continuation

    // MARK: Private
    private let url: URL
    private let authToken: String
    private let sessionId: String

    private var task: URLSessionWebSocketTask?
    private var stopped = false

    // MARK: Init

    init(url: URL, authToken: String, sessionId: String) {
        self.url = url
        self.authToken = authToken
        self.sessionId = sessionId
        (state, stateContinuation) = AsyncStream<ConnectionState>.makeStream()
    }

    // MARK: Public API

    func start() async {
        stopped = false
        await connect()
    }

    func stop() async {
        stopped = true
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
        stateContinuation.yield(.closed)
        stateContinuation.finish()
    }

    /// Send a raw H.264 NAL unit or audio PCM chunk as a binary WebSocket frame.
    /// The orchestrator forwards it verbatim to the Gemini Live session.
    func sendBinary(_ data: Data) async throws {
        guard let t = task else { throw LiveSocketError.notConnected }
        try await t.send(.data(data))
    }

    // MARK: Connection lifecycle

    private func connect() async {
        var backoff = BackoffPolicy()

        while !stopped {
            stateContinuation.yield(.connecting)
            Log.net.info("live socket connecting to \(self.url.absoluteString, privacy: .public)")

            let session = URLSession(configuration: .default)
            // Auth: offer the bare token as a second subprotocol entry so the
            // server's _auth_subprotocol matcher (exact equality against
            // ALLOWED_DEV_TOKENS) can find it.  "forge.live.v2" stays first as
            // the application-level protocol identifier (specs/00 §8).
            let wsTask = session.webSocketTask(
                with: liveURL(),
                protocols: ["forge.live.v2", authToken]
            )
            task = wsTask
            wsTask.resume()

            // Drain the receive loop (server sends no meaningful frames on this
            // channel; we just need to detect disconnection).
            let connected = await receiveLoop(task: wsTask)

            if stopped { break }

            if connected { backoff.reset() }
            let delay = backoff.next()
            Log.net.notice("live socket lost — retrying in \(Int(delay))s")
            stateContinuation.yield(.degraded(reason: "live socket disconnected — retrying in \(Int(delay))s"))
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
        }
    }

    private func liveURL() -> URL {
        guard var comps = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return url }
        comps.queryItems = (comps.queryItems ?? []) + [URLQueryItem(name: "sessionId", value: sessionId)]
        return comps.url ?? url
    }

    @discardableResult
    private func receiveLoop(task wsTask: URLSessionWebSocketTask) async -> Bool {
        var didOpen = false
        while !stopped {
            do {
                let message = try await wsTask.receive()
                if !didOpen {
                    didOpen = true
                    stateContinuation.yield(.open)
                    Log.net.info("live socket open")
                }
                // Server sends no meaningful messages on the live channel;
                // we only need to stay connected.
                _ = message
            } catch {
                return didOpen
            }
        }
        return didOpen
    }
}

// MARK: - Error

private enum LiveSocketError: Error {
    case notConnected
}
