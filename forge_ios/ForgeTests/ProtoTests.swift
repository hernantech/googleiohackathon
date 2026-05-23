import XCTest
@testable import Forge

// WITHOUT BACKEND — pure wire-protocol logic. Round-trips, discriminator casing,
// forward-compatibility, and typed-card parsing. No network.
final class ProtoTests: XCTestCase {

    private func roundTrip(_ e: AgentEvent) throws -> AgentEvent {
        try AgentEvent.decode(from: e.jsonData())
    }

    func testHelloCarriesProtocolVersion() throws {
        let e = AgentEvent.hello(client: "ios", sessionId: "s1", protocolVersion: "2.0")
        let json = String(decoding: try e.jsonData(), as: UTF8.self)
        XCTAssertTrue(json.contains("\"kind\":\"Hello\""), json)
        XCTAssertTrue(json.contains("\"protocolVersion\":\"2.0\""), json)
        XCTAssertEqual(try roundTrip(e), e)
    }

    func testV1CarryoverRoundTrips() throws {
        let events: [AgentEvent] = [
            .transcript(text: "hi", partial: false, ts: 7, speaker: .live, smeId: nil),
            .transcript(text: "claim", partial: true, ts: 9, speaker: .sme, smeId: "@power"),
            .toolCall(name: "look_at_bench", argsJson: "{}", callId: "c1"),
            .toolResult(callId: "c1", resultJson: "{}", deferred: true),
            .confirmationRequest(callId: "c2", summary: "set 3.3V", risk: .high, invokerSmeId: "@power", actionCardJson: nil),
            .confirmationResponse(callId: "c2", approved: true, approverChannel: .chat),
            .audioChunk(pcmBase64: "AAAA", ts: 11),
            .goodbye(reason: "bye"),
        ]
        for e in events { XCTAssertEqual(try roundTrip(e), e, "\(e)") }
    }

    func testV2EventsRoundTrip() throws {
        let cm = ChatMessage(channelId: "#dissent", authorId: "@power", authorKind: .sme,
                             body: "we disagree", bodyContentType: .markdown, mentions: ["@signal"],
                             replyToId: nil, messageId: "m1", ts: 5, streaming: true)
        let events: [AgentEvent] = [
            .chatMessage(cm),
            .channelUpdate(ChannelUpdate(messageId: "m1", deltaText: "…", done: true, ts: 6)),
            .channelList(ChannelList(channels: [ChannelInfo(id: "#general", title: "General")])),
            .ping(nonce: "n1"),
            .pong(nonce: "n1"),
            .subscribe(channelId: "#power"),
            .errorEvent(ErrorEvent(code: "rate_limited", message: "slow down", causedByMessageId: nil, ts: 1)),
        ]
        for e in events { XCTAssertEqual(try roundTrip(e), e, "\(e)") }
    }

    func testDiscriminatorIsPascalCase() throws {
        // The wire "kind" must match the Python orchestrator's Literal values.
        let json = String(decoding: try AgentEvent.chatMessage(
            ChatMessage(channelId: "#general", authorId: "@u", authorKind: .user,
                        body: "hi", messageId: "m9", ts: 1)).jsonData(), as: UTF8.self)
        XCTAssertTrue(json.contains("\"kind\":\"ChatMessage\""), json)
    }

    func testUnknownKindThrowsForForwardCompat() {
        let data = Data(#"{"kind":"SomeFutureEventV3","x":1}"#.utf8)
        XCTAssertThrowsError(try AgentEvent.decode(from: data)) { err in
            XCTAssertEqual(err as? AgentEventDecodingError, .unknownKind("SomeFutureEventV3"))
        }
    }

    func testAdditiveFieldsDefaultWhenAbsent() throws {
        // A v1-shaped Transcript (no speaker/smeId) must still parse.
        let tr = try AgentEvent.decode(from: Data(#"{"kind":"Transcript","text":"hi","partial":false,"ts":3}"#.utf8))
        XCTAssertEqual(tr, .transcript(text: "hi", partial: false, ts: 3, speaker: .user, smeId: nil))

        // A minimal ChatMessage gets markdown/[]/false defaults.
        let cm = try AgentEvent.decode(from: Data(##"{"kind":"ChatMessage","channelId":"#general","authorId":"@u","authorKind":"user","body":"hi","messageId":"m1","ts":1}"##.utf8))
        guard case let .chatMessage(m) = cm else { return XCTFail("expected chatMessage") }
        XCTAssertEqual(m.bodyContentType, .markdown)
        XCTAssertEqual(m.mentions, [])
        XCTAssertFalse(m.streaming)
    }

    func testActionCardFromJSON() {
        let json = #"{"kind":"ActionCard","title":"Set PSU","bodyMarkdown":"3.3V","risk":"HIGH"}"#
        let card = ActionCard.from(json: json)
        XCTAssertEqual(card?.title, "Set PSU")
        XCTAssertEqual(card?.risk, .high)
        // v2 defaults per specs/00 §2.1 (WP-7): "I did it" / "Skip"
        XCTAssertEqual(card?.affirmLabel, "I did it")
        XCTAssertEqual(card?.denyLabel, "Skip")
        XCTAssertNil(ActionCard.from(json: nil))
    }

    func testChatCardParsesSmeResponse() {
        let body = #"{"kind":"SmeResponse","smeId":"@power","callId":"c1","confidence":0.9,"claim":"VDD sags","rationale":"...","ts":1}"#
        guard case let .smeResponse(r)? = ChatCard.parse(body) else { return XCTFail("expected smeResponse card") }
        XCTAssertEqual(r.smeId, "@power")
        XCTAssertEqual(r.confidence, 0.9, accuracy: 0.001)
        XCTAssertEqual(r.evidence, [])   // defaulted
    }

    func testLookAtBenchResultDecodes() {
        let json = #"{"components":[{"id":"U1","partNumber":"STM32F411","bbox":{"x1":1,"y1":2,"x2":3,"y2":4},"confidence":0.8}],"sceneSummary":"a board"}"#
        let result = LookAtBenchResult.from(resultJson: json)
        XCTAssertEqual(result?.components.count, 1)
        XCTAssertEqual(result?.components.first?.id, "U1")
        XCTAssertEqual(result?.components.first?.confidence ?? 0, 0.8, accuracy: 0.001)
    }
}
