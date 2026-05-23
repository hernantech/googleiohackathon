import Foundation

// Hand-written Codable for the AgentEvent union (v2). The wire format uses a
// "kind" discriminator whose values are PascalCase, identical to the Python
// orchestrator (`Literal["Transcript"]`, …). Field names are camelCase.
//
// Forward compatibility: unknown `kind` values throw
// `AgentEventDecodingError.unknownKind`, which the socket layer drops rather
// than crashing. v2-additive fields decode tolerantly (decodeIfPresent +
// defaults) so older payloads still parse.

enum AgentEventDecodingError: Error, Equatable {
    case unknownKind(String)
    case missingKind
}

extension AgentEvent: Codable {
    private enum Kind: String {
        case transcript = "Transcript"
        case toolCall = "ToolCall"
        case toolResult = "ToolResult"
        case confirmationRequest = "ConfirmationRequest"
        case confirmationResponse = "ConfirmationResponse"
        case audioChunk = "AudioChunk"
        case hello = "Hello"
        case goodbye = "Goodbye"
        case chatMessage = "ChatMessage"
        case channelUpdate = "ChannelUpdate"
        case channelList = "ChannelList"
        case channelHint = "ChannelHint"
        case summonGuild = "SummonGuild"
        case smeResponse = "SmeResponse"
        case dissentReport = "DissentReport"
        case safetyInterrupt = "SafetyInterrupt"
        case checkpointMarker = "CheckpointMarker"
        case replayDone = "ReplayDone"
        case backpressureNotice = "BackpressureNotice"
        case errorEvent = "ErrorEvent"
        case ping = "Ping"
        case pong = "Pong"
        case subscribe = "Subscribe"
        case unsubscribe = "Unsubscribe"
    }

    private enum CodingKeys: String, CodingKey {
        case kind
        case text, partial, ts, speaker, smeId
        case name, argsJson, callId
        case resultJson, deferred
        case summary, risk, invokerSmeId, actionCardJson
        case approved, approverChannel
        case pcmBase64
        case client, sessionId, protocolVersion
        case reason
        case nonce
        case channelId
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let raw = try c.decodeIfPresent(String.self, forKey: .kind) else {
            throw AgentEventDecodingError.missingKind
        }
        guard let kind = Kind(rawValue: raw) else {
            throw AgentEventDecodingError.unknownKind(raw)
        }
        switch kind {
        case .transcript:
            self = .transcript(
                text: try c.decode(String.self, forKey: .text),
                partial: try c.decode(Bool.self, forKey: .partial),
                ts: try c.decode(Int64.self, forKey: .ts),
                speaker: try c.decodeIfPresent(Speaker.self, forKey: .speaker) ?? .user,
                smeId: try c.decodeIfPresent(String.self, forKey: .smeId)
            )
        case .toolCall:
            self = .toolCall(
                name: try c.decode(String.self, forKey: .name),
                argsJson: try c.decode(String.self, forKey: .argsJson),
                callId: try c.decode(String.self, forKey: .callId)
            )
        case .toolResult:
            self = .toolResult(
                callId: try c.decode(String.self, forKey: .callId),
                resultJson: try c.decode(String.self, forKey: .resultJson),
                deferred: try c.decodeIfPresent(Bool.self, forKey: .deferred) ?? false
            )
        case .confirmationRequest:
            self = .confirmationRequest(
                callId: try c.decode(String.self, forKey: .callId),
                summary: try c.decode(String.self, forKey: .summary),
                risk: try c.decode(Risk.self, forKey: .risk),
                invokerSmeId: try c.decodeIfPresent(String.self, forKey: .invokerSmeId),
                actionCardJson: try c.decodeIfPresent(String.self, forKey: .actionCardJson)
            )
        case .confirmationResponse:
            self = .confirmationResponse(
                callId: try c.decode(String.self, forKey: .callId),
                approved: try c.decode(Bool.self, forKey: .approved),
                approverChannel: try c.decodeIfPresent(ApproverChannel.self, forKey: .approverChannel) ?? .voice
            )
        case .audioChunk:
            self = .audioChunk(
                pcmBase64: try c.decode(String.self, forKey: .pcmBase64),
                ts: try c.decode(Int64.self, forKey: .ts)
            )
        case .hello:
            self = .hello(
                client: try c.decode(String.self, forKey: .client),
                sessionId: try c.decode(String.self, forKey: .sessionId),
                protocolVersion: try c.decodeIfPresent(String.self, forKey: .protocolVersion) ?? "2.0"
            )
        case .goodbye:
            self = .goodbye(reason: try c.decode(String.self, forKey: .reason))
        case .chatMessage:
            self = .chatMessage(try ChatMessage(from: decoder))
        case .channelUpdate:
            self = .channelUpdate(try ChannelUpdate(from: decoder))
        case .channelList:
            self = .channelList(try ChannelList(from: decoder))
        case .channelHint:
            self = .channelHint(try ChannelHint(from: decoder))
        case .summonGuild:
            self = .summonGuild(try SummonGuild(from: decoder))
        case .smeResponse:
            self = .smeResponse(try SmeResponse(from: decoder))
        case .dissentReport:
            self = .dissentReport(try DissentReport(from: decoder))
        case .safetyInterrupt:
            self = .safetyInterrupt(try SafetyInterrupt(from: decoder))
        case .checkpointMarker:
            self = .checkpointMarker(try CheckpointMarker(from: decoder))
        case .replayDone:
            self = .replayDone(try ReplayDone(from: decoder))
        case .backpressureNotice:
            self = .backpressureNotice(try BackpressureNotice(from: decoder))
        case .errorEvent:
            self = .errorEvent(try ErrorEvent(from: decoder))
        case .ping:
            self = .ping(nonce: try c.decodeIfPresent(String.self, forKey: .nonce) ?? "")
        case .pong:
            self = .pong(nonce: try c.decodeIfPresent(String.self, forKey: .nonce) ?? "")
        case .subscribe:
            self = .subscribe(channelId: try c.decode(String.self, forKey: .channelId))
        case .unsubscribe:
            self = .unsubscribe(channelId: try c.decode(String.self, forKey: .channelId))
        }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        switch self {
        case let .transcript(text, partial, ts, speaker, smeId):
            try c.encode(Kind.transcript.rawValue, forKey: .kind)
            try c.encode(text, forKey: .text)
            try c.encode(partial, forKey: .partial)
            try c.encode(ts, forKey: .ts)
            try c.encode(speaker, forKey: .speaker)
            try c.encodeIfPresent(smeId, forKey: .smeId)
        case let .toolCall(name, argsJson, callId):
            try c.encode(Kind.toolCall.rawValue, forKey: .kind)
            try c.encode(name, forKey: .name)
            try c.encode(argsJson, forKey: .argsJson)
            try c.encode(callId, forKey: .callId)
        case let .toolResult(callId, resultJson, deferred):
            try c.encode(Kind.toolResult.rawValue, forKey: .kind)
            try c.encode(callId, forKey: .callId)
            try c.encode(resultJson, forKey: .resultJson)
            try c.encode(deferred, forKey: .deferred)
        case let .confirmationRequest(callId, summary, risk, invokerSmeId, actionCardJson):
            try c.encode(Kind.confirmationRequest.rawValue, forKey: .kind)
            try c.encode(callId, forKey: .callId)
            try c.encode(summary, forKey: .summary)
            try c.encode(risk, forKey: .risk)
            try c.encodeIfPresent(invokerSmeId, forKey: .invokerSmeId)
            try c.encodeIfPresent(actionCardJson, forKey: .actionCardJson)
        case let .confirmationResponse(callId, approved, approverChannel):
            try c.encode(Kind.confirmationResponse.rawValue, forKey: .kind)
            try c.encode(callId, forKey: .callId)
            try c.encode(approved, forKey: .approved)
            try c.encode(approverChannel, forKey: .approverChannel)
        case let .audioChunk(pcmBase64, ts):
            try c.encode(Kind.audioChunk.rawValue, forKey: .kind)
            try c.encode(pcmBase64, forKey: .pcmBase64)
            try c.encode(ts, forKey: .ts)
        case let .hello(client, sessionId, protocolVersion):
            try c.encode(Kind.hello.rawValue, forKey: .kind)
            try c.encode(client, forKey: .client)
            try c.encode(sessionId, forKey: .sessionId)
            try c.encode(protocolVersion, forKey: .protocolVersion)
        case let .goodbye(reason):
            try c.encode(Kind.goodbye.rawValue, forKey: .kind)
            try c.encode(reason, forKey: .reason)
        case let .ping(nonce):
            try c.encode(Kind.ping.rawValue, forKey: .kind)
            try c.encode(nonce, forKey: .nonce)
        case let .pong(nonce):
            try c.encode(Kind.pong.rawValue, forKey: .kind)
            try c.encode(nonce, forKey: .nonce)
        case let .subscribe(channelId):
            try c.encode(Kind.subscribe.rawValue, forKey: .kind)
            try c.encode(channelId, forKey: .channelId)
        case let .unsubscribe(channelId):
            try c.encode(Kind.unsubscribe.rawValue, forKey: .kind)
            try c.encode(channelId, forKey: .channelId)
        // Struct-backed cases: encode the payload, then stamp the discriminator.
        case let .chatMessage(p):       try encodeStruct(p, kind: .chatMessage, to: encoder)
        case let .channelUpdate(p):     try encodeStruct(p, kind: .channelUpdate, to: encoder)
        case let .channelList(p):       try encodeStruct(p, kind: .channelList, to: encoder)
        case let .channelHint(p):       try encodeStruct(p, kind: .channelHint, to: encoder)
        case let .summonGuild(p):       try encodeStruct(p, kind: .summonGuild, to: encoder)
        case let .smeResponse(p):       try encodeStruct(p, kind: .smeResponse, to: encoder)
        case let .dissentReport(p):     try encodeStruct(p, kind: .dissentReport, to: encoder)
        case let .safetyInterrupt(p):   try encodeStruct(p, kind: .safetyInterrupt, to: encoder)
        case let .checkpointMarker(p):  try encodeStruct(p, kind: .checkpointMarker, to: encoder)
        case let .replayDone(p):        try encodeStruct(p, kind: .replayDone, to: encoder)
        case let .backpressureNotice(p): try encodeStruct(p, kind: .backpressureNotice, to: encoder)
        case let .errorEvent(p):        try encodeStruct(p, kind: .errorEvent, to: encoder)
        }
    }

    private func encodeStruct<T: Encodable>(_ value: T, kind: Kind, to encoder: Encoder) throws {
        try value.encode(to: encoder)
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(kind.rawValue, forKey: .kind)
    }
}

extension AgentEvent {
    /// JSON-encode this event for a WS text frame.
    func jsonData() throws -> Data {
        try JSONEncoder().encode(self)
    }

    /// Decode an event from a WS text frame. Throws `AgentEventDecodingError`
    /// for kinds this client does not model; callers drop those.
    static func decode(from data: Data) throws -> AgentEvent {
        try JSONDecoder().decode(AgentEvent.self, from: data)
    }
}
