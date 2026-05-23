import Foundation
import Network
@testable import Forge

/// In-process WebSocket server standing in for the orchestrator, so the
/// "with backend" tests run deterministically against a real local socket
/// (NWListener + NWProtocolWebSocket). Mirrors the v2 chat-bus framing.
final class MockOrchestrator: @unchecked Sendable {

    private let listener: NWListener
    private let queue = DispatchQueue(label: "ai.forge.mock")
    private let lock = NSLock()

    private var conn: NWConnection?
    private var _texts: [String] = []
    private var _binaries: [Data] = []
    private var _ready = false
    private var _port: UInt16 = 0

    /// When true, replies to a received Hello with a ChannelList automatically.
    var autoChannelListOnHello = true

    init() throws {
        let params = NWParameters.tcp
        let ws = NWProtocolWebSocket.Options()
        ws.autoReplyPing = true
        params.defaultProtocolStack.applicationProtocols.insert(ws, at: 0)
        listener = try NWListener(using: params)   // OS-assigned ephemeral port
        listener.stateUpdateHandler = { [weak self] state in
            guard let self else { return }
            if case .ready = state {
                self.lock.lock()
                self._ready = true
                self._port = self.listener.port?.rawValue ?? 0
                self.lock.unlock()
            }
        }
        listener.newConnectionHandler = { [weak self] c in self?.accept(c) }
        listener.start(queue: queue)
    }

    // MARK: Readiness / addressing

    var port: UInt16 { lock.lock(); defer { lock.unlock() }; return _port }
    func url(path: String) -> URL { URL(string: "ws://127.0.0.1:\(port)\(path)")! }

    func waitReady(timeout: TimeInterval = 3) async throws {
        try await waitUntil(timeout: timeout) { self.lock.lock(); defer { self.lock.unlock() }; return self._ready && self._port != 0 }
    }

    // MARK: Inbound capture

    func receivedTexts() -> [String] { lock.lock(); defer { lock.unlock() }; return _texts }
    func receivedBinaries() -> [Data] { lock.lock(); defer { lock.unlock() }; return _binaries }
    func helloCount() -> Int { receivedTexts().filter { $0.contains("\"kind\":\"Hello\"") }.count }

    // MARK: Outbound

    func send(_ event: AgentEvent) {
        guard let data = try? event.jsonData() else { return }
        sendText(String(decoding: data, as: UTF8.self))
    }

    func sendText(_ text: String) {
        lock.lock(); let c = conn; lock.unlock()
        guard let c else { return }
        let meta = NWProtocolWebSocket.Metadata(opcode: .text)
        let ctx = NWConnection.ContentContext(identifier: "text", metadata: [meta])
        c.send(content: text.data(using: .utf8), contentContext: ctx, isComplete: true, completion: .contentProcessed { _ in })
    }

    func stop() {
        listener.cancel()
        lock.lock(); conn?.cancel(); conn = nil; lock.unlock()
    }

    // MARK: Connection handling

    private func accept(_ c: NWConnection) {
        lock.lock(); conn = c; lock.unlock()
        c.start(queue: queue)
        receive(on: c)
    }

    private func receive(on c: NWConnection) {
        c.receiveMessage { [weak self] data, context, _, error in
            guard let self else { return }
            if let data,
               let meta = context?.protocolMetadata(definition: NWProtocolWebSocket.definition) as? NWProtocolWebSocket.Metadata {
                switch meta.opcode {
                case .text:
                    let text = String(decoding: data, as: UTF8.self)
                    self.lock.lock(); self._texts.append(text); self.lock.unlock()
                    if self.autoChannelListOnHello, text.contains("\"kind\":\"Hello\"") {
                        self.send(.channelList(ChannelList(channels: [
                            ChannelInfo(id: "#general", title: "General", alwaysVisible: true)
                        ])))
                    }
                case .binary:
                    self.lock.lock(); self._binaries.append(data); self.lock.unlock()
                default:
                    break
                }
            }
            if error == nil { self.receive(on: c) }
        }
    }
}
