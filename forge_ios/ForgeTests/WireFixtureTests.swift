import XCTest
@testable import Forge

// WP-6 (client side) — every testdata/wire/*.json fixture must decode without
// error in the Swift AgentEvent / card deserializers.  Field values are spot-
// checked against the golden corpus to catch field-name mismatches.
//
// The fixture JSON files are embedded in the test bundle via the ForgeTests
// Copy Files build phase (see project.yml / Forge.xcodeproj).  When running
// outside Xcode (e.g. xcodebuild) they must be present in the bundle's
// Resources folder.  The test gracefully skips any fixture that is not found
// so CI does not false-fail on a missing resource.
final class WireFixtureTests: XCTestCase {

    // MARK: - Helpers

    private func fixture(named name: String) throws -> Data {
        // Look in the test bundle's "wire" subdirectory first (fixtures are
        // copied there by the ForgeTests Copy Files build phase).
        if let url = Bundle(for: WireFixtureTests.self)
            .url(forResource: name, withExtension: "json", subdirectory: "wire") {
            return try Data(contentsOf: url)
        }
        // Relative fallback: <repo>/testdata/wire/<name>.json (swift test / local runs).
        let srcDir = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // ForgeTests/
            .deletingLastPathComponent()   // forge_ios/
            .deletingLastPathComponent()   // repo root
            .appendingPathComponent("testdata/wire/\(name).json")
        if FileManager.default.fileExists(atPath: srcDir.path) {
            return try Data(contentsOf: srcDir)
        }
        throw XCTSkip("Fixture '\(name).json' not found in bundle or testdata/wire/")
    }

    private func decodeEvent(named name: String) throws -> AgentEvent {
        let data = try fixture(named: name)
        return try AgentEvent.decode(from: data)
    }

    private func decodeCard<T: Decodable>(named name: String, as type: T.Type) throws -> T {
        let data = try fixture(named: name)
        return try JSONDecoder().decode(type, from: data)
    }

    // MARK: - AgentEvent fixtures

    func testHelloFixture() throws {
        let event = try decodeEvent(named: "Hello")
        guard case let .hello(client, sessionId, protocolVersion) = event else {
            return XCTFail("expected .hello, got \(event)")
        }
        XCTAssertEqual(client, "phone")
        XCTAssertEqual(sessionId, "01HSESSION")
        XCTAssertEqual(protocolVersion, "2.0")
    }

    func testGoodbyeFixture() throws {
        let event = try decodeEvent(named: "Goodbye")
        guard case let .goodbye(reason) = event else {
            return XCTFail("expected .goodbye")
        }
        XCTAssertEqual(reason, "protocol_mismatch")
    }

    func testTranscriptFixture() throws {
        let event = try decodeEvent(named: "Transcript")
        guard case let .transcript(text, partial, ts, speaker, smeId) = event else {
            return XCTFail("expected .transcript")
        }
        XCTAssertEqual(text, "ESP32 can't read the BQ79616.")
        XCTAssertFalse(partial)
        XCTAssertEqual(ts, 1_716_500_000_000_000_000)
        XCTAssertEqual(speaker, .user)
        XCTAssertNil(smeId)
    }

    func testToolCallFixture() throws {
        let event = try decodeEvent(named: "ToolCall")
        guard case let .toolCall(name, argsJson, callId) = event else {
            return XCTFail("expected .toolCall")
        }
        XCTAssertEqual(name, "summon_guild")
        XCTAssertEqual(callId, "01HCALL")
        XCTAssertTrue(argsJson.contains("comm-timeout"))
    }

    func testToolResultFixture() throws {
        let event = try decodeEvent(named: "ToolResult")
        guard case let .toolResult(callId, resultJson, deferred) = event else {
            return XCTFail("expected .toolResult")
        }
        XCTAssertEqual(callId, "01HCALL")
        XCTAssertTrue(deferred)
        XCTAssertTrue(resultJson.contains("01HJOB"))
    }

    func testConfirmationRequestFixture() throws {
        let event = try decodeEvent(named: "ConfirmationRequest")
        guard case let .confirmationRequest(callId, summary, risk, invokerSmeId, actionCardJson) = event else {
            return XCTFail("expected .confirmationRequest")
        }
        XCTAssertEqual(callId, "01HCALL")
        XCTAssertEqual(risk, .high)
        XCTAssertEqual(invokerSmeId, "@power")
        XCTAssertNotNil(actionCardJson)
        // The nested ActionCard must also parse (WP-7: defaults applied).
        let card = ActionCard.from(json: actionCardJson)
        XCTAssertEqual(card?.affirmLabel, "I did it")
        XCTAssertEqual(card?.denyLabel, "Skip")
        XCTAssertEqual(card?.risk, .high)
        XCTAssertNotNil(card?.documentedLimit)
        _ = summary  // used
    }

    func testConfirmationResponseFixture() throws {
        let event = try decodeEvent(named: "ConfirmationResponse")
        guard case let .confirmationResponse(callId, approved, approverChannel) = event else {
            return XCTFail("expected .confirmationResponse")
        }
        XCTAssertEqual(callId, "01HCALL")
        XCTAssertTrue(approved)
        XCTAssertEqual(approverChannel, .voice)
    }

    func testAudioChunkFixture() throws {
        let event = try decodeEvent(named: "AudioChunk")
        guard case let .audioChunk(pcmBase64, ts) = event else {
            return XCTFail("expected .audioChunk")
        }
        XCTAssertEqual(pcmBase64, "AAAA")
        XCTAssertEqual(ts, 1_716_500_000_000_000_000)
    }

    func testChatMessageFixture() throws {
        let event = try decodeEvent(named: "ChatMessage")
        guard case let .chatMessage(msg) = event else {
            return XCTFail("expected .chatMessage")
        }
        XCTAssertEqual(msg.channelId, "#power")
        XCTAssertEqual(msg.authorId, "@power")
        XCTAssertEqual(msg.authorKind, .sme)
        XCTAssertEqual(msg.bodyContentType, .markdown)
        XCTAssertEqual(msg.mentions, ["@signal"])
        XCTAssertEqual(msg.messageId, "01HMSG")
        XCTAssertFalse(msg.streaming)
    }

    func testSummonGuildFixture() throws {
        let event = try decodeEvent(named: "SummonGuild")
        guard case let .summonGuild(sg) = event else {
            return XCTFail("expected .summonGuild")
        }
        XCTAssertEqual(sg.callId, "01HCALL")
        XCTAssertEqual(sg.topic, "bq79616-comm-timeout")
        XCTAssertEqual(sg.smes, ["@firmware", "@signal", "@power"])
        XCTAssertEqual(sg.deadlineMs, 30_000)
        XCTAssertFalse(sg.contextRefs.isEmpty)
    }

    func testSmeResponseFixture() throws {
        let event = try decodeEvent(named: "SmeResponse")
        guard case let .smeResponse(sr) = event else {
            return XCTFail("expected .smeResponse")
        }
        XCTAssertEqual(sr.smeId, "@power")
        XCTAssertEqual(sr.callId, "01HCALL")
        XCTAssertEqual(sr.confidence, 0.92, accuracy: 0.001)
        XCTAssertFalse(sr.evidence.isEmpty)
        XCTAssertEqual(sr.dissentsWith, ["@firmware"])
        // ProposedAction v2 fields
        let operatorAction = sr.proposedActions.first { $0.actor == .operator }
        XCTAssertNotNil(operatorAction)
        XCTAssertEqual(operatorAction?.risk, .high)
        XCTAssertNotNil(operatorAction?.instruction)
        XCTAssertNotNil(operatorAction?.documentedLimitRef)
        let guildAction = sr.proposedActions.first { $0.actor == .guild }
        XCTAssertNotNil(guildAction)
        XCTAssertEqual(guildAction?.risk, .low)
    }

    func testDissentReportFixture() throws {
        let event = try decodeEvent(named: "DissentReport")
        guard case let .dissentReport(dr) = event else {
            return XCTFail("expected .dissentReport")
        }
        XCTAssertEqual(dr.callId, "01HCALL")
        XCTAssertEqual(dr.axis, "root_cause")
        XCTAssertEqual(dr.parties, ["@power", "@firmware"])
        XCTAssertEqual(dr.pairwise.count, 1)
        let pair = dr.pairwise[0]
        XCTAssertEqual(pair.a, "@power")
        XCTAssertEqual(pair.b, "@firmware")
    }

    func testChannelUpdateFixture() throws {
        let event = try decodeEvent(named: "ChannelUpdate")
        guard case let .channelUpdate(cu) = event else {
            return XCTFail("expected .channelUpdate")
        }
        XCTAssertEqual(cu.messageId, "01HMSG")
        XCTAssertFalse(cu.done)
        XCTAssertEqual(cu.ts, 1_716_500_000_000_000_000)
    }

    func testSafetyInterruptFixture() throws {
        let event = try decodeEvent(named: "SafetyInterrupt")
        guard case let .safetyInterrupt(si) = event else {
            return XCTFail("expected .safetyInterrupt")
        }
        XCTAssertEqual(si.severity, .warn)
        XCTAssertFalse(si.reason.isEmpty)
        XCTAssertEqual(si.suggestedRecoverActions.count, 1)
        let action = si.suggestedRecoverActions[0]
        XCTAssertEqual(action.actor, .operator)
        XCTAssertNotNil(action.instruction)
    }

    func testCheckpointMarkerFixture() throws {
        let event = try decodeEvent(named: "CheckpointMarker")
        guard case let .checkpointMarker(cm) = event else {
            return XCTFail("expected .checkpointMarker")
        }
        XCTAssertEqual(cm.checkpointId, "01HCKPT")
        XCTAssertEqual(cm.graphNodeName, "SafetyGate")
        XCTAssertEqual(cm.ts, 1_716_500_000_000_000_000)
    }

    // MARK: - Card-type fixtures (not AgentEvent members)

    func testActionCardFixture() throws {
        let data = try fixture(named: "ActionCard")
        let card = try JSONDecoder().decode(ActionCard.self, from: data)
        XCTAssertEqual(card.affirmLabel, "I did it")
        XCTAssertEqual(card.denyLabel, "Skip")
        XCTAssertEqual(card.risk, .high)
        XCTAssertNotNil(card.documentedLimit)
        XCTAssertTrue(card.title.contains("@power"))
    }

    func testFrameRefFixture() throws {
        let data = try fixture(named: "FrameRef")
        let ref = try JSONDecoder().decode(FrameRef.self, from: data)
        XCTAssertEqual(ref.width, 1920)
        XCTAssertEqual(ref.height, 1080)
        XCTAssertEqual(ref.sourceSeq, 1)
        XCTAssertEqual(ref.ts, 1_716_500_000_000_000_000)
        XCTAssertFalse(ref.uri.isEmpty)
    }

    /// WP-11: SnapshotAnalysis round-trips; embeds a valid FrameRef; cites defaults to [].
    func testSnapshotAnalysisFixture() throws {
        let data = try fixture(named: "SnapshotAnalysis")
        let sa = try JSONDecoder().decode(SnapshotAnalysis.self, from: data)
        XCTAssertEqual(sa.jobId, "01HJOB")
        XCTAssertEqual(sa.model, "gemini-3-pro")
        XCTAssertFalse(sa.analysis.isEmpty)
        XCTAssertEqual(sa.frame.width, 1920)
        XCTAssertEqual(sa.frame.height, 1080)
        XCTAssertEqual(sa.frame.sourceSeq, 1)
        XCTAssertEqual(sa.cites.count, 1)
        XCTAssertEqual(sa.ts, 1_716_500_000_000_000_000)

        // ChatCard parser also handles SnapshotAnalysis bodies.
        let body = String(decoding: data, as: UTF8.self)
        guard case .snapshotAnalysis(let parsed)? = ChatCard.parse(body) else {
            return XCTFail("ChatCard.parse should return .snapshotAnalysis")
        }
        XCTAssertEqual(parsed.jobId, sa.jobId)
    }

    // MARK: - ProposedAction defaults (WP-5)

    func testProposedActionActorDefaultsToOperator() throws {
        // A ProposedAction without an `actor` field must default to "operator".
        let json = Data(#"{"tool":"set_psu","argsJson":"{}","rationale":"r","risk":"LOW"}"#.utf8)
        let pa = try JSONDecoder().decode(ProposedAction.self, from: json)
        XCTAssertEqual(pa.actor, .operator)
    }

    func testProposedActionGuildActor() throws {
        let json = Data(#"{"actor":"guild","tool":"lookup_datasheet","argsJson":"{}","rationale":"r","risk":"LOW"}"#.utf8)
        let pa = try JSONDecoder().decode(ProposedAction.self, from: json)
        XCTAssertEqual(pa.actor, .guild)
    }
}
