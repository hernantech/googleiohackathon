import XCTest
@testable import Forge

// WITH BACKEND — exercises the real OrchestratorSocket against an in-process
// WebSocket server (MockOrchestrator). Covers the v2 handshake, routing,
// Ping/Pong, binary framing, and fatal-close-no-reconnect.
final class OrchestratorSocketIntegrationTests: XCTestCase {

    private func drain(_ socket: OrchestratorSocket) async -> (states: Box<ConnectionState>, events: Box<AgentEvent>, tasks: [Task<Void, Never>]) {
        let states = Box<ConnectionState>()
        let events = Box<AgentEvent>()
        let stateStream = await socket.state
        let eventStream = await socket.events
        let t1 = Task { for await s in stateStream { states.add(s) } }
        let t2 = Task { for await e in eventStream { events.add(e) } }
        return (states, events, [t1, t2])
    }

    private func makeSocket(_ mock: MockOrchestrator, session: String = "s1") -> OrchestratorSocket {
        OrchestratorSocket(url: mock.url(path: "/v2/chat"), authToken: "dev-token", sessionId: session)
    }

    // MARK: Handshake

    func testHelloHandshakeCarriesProtocolVersionAndOpens() async throws {
        let mock = try MockOrchestrator(); try await mock.waitReady()
        defer { mock.stop() }
        let socket = makeSocket(mock)
        let (states, _, tasks) = await drain(socket)
        defer { tasks.forEach { $0.cancel() } }

        await socket.start()
        try await waitUntil { mock.helloCount() >= 1 }
        let hello = mock.receivedTexts().first { $0.contains("\"kind\":\"Hello\"") } ?? ""
        XCTAssertTrue(hello.contains("\"protocolVersion\":\"2.0\""), hello)
        XCTAssertTrue(hello.contains("\"client\":\"ios\""), hello)

        // .open only after the server's first frame (ChannelList) arrives.
        try await waitUntil { states.all().contains(.open) }
        await socket.stop()
    }

    func testStaysConnectingUntilFirstFrame() async throws {
        let mock = try MockOrchestrator()
        mock.autoChannelListOnHello = false        // server stays silent after Hello
        try await mock.waitReady()
        defer { mock.stop() }
        let socket = makeSocket(mock)
        let (states, _, tasks) = await drain(socket)
        defer { tasks.forEach { $0.cancel() } }

        await socket.start()
        try await waitUntil { mock.helloCount() >= 1 }
        // Give it room: with no inbound frame, it must NOT claim .open.
        try? await Task.sleep(nanoseconds: 700_000_000)
        XCTAssertFalse(states.all().contains(.open), "open was announced before any frame arrived")

        // Once the server speaks, the socket opens.
        mock.send(.channelList(ChannelList(channels: [ChannelInfo(id: "#general", title: "General")])))
        try await waitUntil { states.all().contains(.open) }
        await socket.stop()
    }

    // MARK: Routing

    func testChannelListIsRouted() async throws {
        let mock = try MockOrchestrator(); try await mock.waitReady()
        defer { mock.stop() }
        let socket = makeSocket(mock)
        let (_, events, tasks) = await drain(socket)
        defer { tasks.forEach { $0.cancel() } }

        await socket.start()
        try await waitUntil {
            events.all().contains { if case .channelList = $0 { return true } else { return false } }
        }
        await socket.stop()
    }

    func testToolResultDelivered() async throws {
        let mock = try MockOrchestrator(); try await mock.waitReady()
        defer { mock.stop() }
        let socket = makeSocket(mock)
        let (states, events, tasks) = await drain(socket)
        defer { tasks.forEach { $0.cancel() } }

        await socket.start()
        try await waitUntil { states.all().contains(.open) }

        let look = #"{"components":[{"id":"U1","partNumber":"STM32F411","bbox":{"x1":1,"y1":1,"x2":2,"y2":2},"confidence":0.9}]}"#
        mock.send(.toolResult(callId: "c1", resultJson: look, deferred: false))

        try await waitUntil {
            events.all().contains { if case .toolResult = $0 { return true } else { return false } }
        }
        let tr = events.all().compactMap { event -> String? in
            if case let .toolResult(_, json, _) = event { return json } else { return nil }
        }.first
        XCTAssertEqual(LookAtBenchResult.from(resultJson: tr ?? "")?.components.first?.id, "U1")
        await socket.stop()
    }

    // MARK: Liveness

    func testClientAutoRepliesToPing() async throws {
        let mock = try MockOrchestrator(); try await mock.waitReady()
        defer { mock.stop() }
        let socket = makeSocket(mock)
        let (states, _, tasks) = await drain(socket)
        defer { tasks.forEach { $0.cancel() } }

        await socket.start()
        try await waitUntil { states.all().contains(.open) }
        mock.send(.ping(nonce: "abc123"))

        try await waitUntil {
            mock.receivedTexts().contains { $0.contains("\"kind\":\"Pong\"") && $0.contains("abc123") }
        }
        await socket.stop()
    }

    func testFatalGoodbyeStopsReconnect() async throws {
        let mock = try MockOrchestrator(); try await mock.waitReady()
        defer { mock.stop() }
        let socket = makeSocket(mock)
        let (states, events, tasks) = await drain(socket)
        defer { tasks.forEach { $0.cancel() } }

        await socket.start()
        try await waitUntil { states.all().contains(.open) }

        mock.send(.goodbye(reason: "protocol_mismatch"))
        try await waitUntil {
            events.all().contains { if case .goodbye = $0 { return true } else { return false } }
        }
        try await waitUntil { states.all().contains(.closed) }

        // Must not reconnect after a fatal close: no second Hello.
        try? await Task.sleep(nanoseconds: 600_000_000)
        XCTAssertEqual(mock.helloCount(), 1)
    }

    // MARK: Binary framing

    func testBinaryFrameHeaderFormat() async throws {
        let mock = try MockOrchestrator(); try await mock.waitReady()
        defer { mock.stop() }
        let socket = makeSocket(mock)
        let (states, _, tasks) = await drain(socket)
        defer { tasks.forEach { $0.cancel() } }

        await socket.start()
        try await waitUntil { states.all().contains(.open) }

        let jpeg = Data([0xFF, 0xD8, 0xFF, 0xE0, 0x11, 0x22])
        try await socket.sendFrame(FrameChunk(jpegBytes: jpeg, widthPx: 640, heightPx: 480, timestampNs: 123_456))

        try await waitUntil { !mock.receivedBinaries().isEmpty }
        let frame = mock.receivedBinaries().first!
        XCTAssertGreaterThanOrEqual(frame.count, 20)
        XCTAssertEqual(String(decoding: frame.prefix(4), as: UTF8.self), "FRAM")
        XCTAssertEqual(int32LE(frame, at: 4), 640)
        XCTAssertEqual(int32LE(frame, at: 8), 480)
        XCTAssertEqual(frame.suffix(jpeg.count), jpeg)
        await socket.stop()
    }

    private func int32LE(_ d: Data, at offset: Int) -> Int32 {
        d.subdata(in: offset ..< offset + 4).withUnsafeBytes { $0.loadUnaligned(as: Int32.self) }
    }
}
