import Foundation

// v2 wire additions. Mirrors specs/00_wire_protocol.md §2 and specs/04_chat_bus_protocol.md.
// These are additive to the v1 envelope in AgentProto.swift. JSON field names are
// camelCase; the AgentEvent "kind" discriminator is handled in AgentProtoCoding.swift.
// Defaulted non-optional fields use custom decoders (Swift's synthesized Decodable
// does not apply property defaults) so partial/forward-compatible payloads still parse.

// MARK: - Enums

enum Speaker: String, Codable, Equatable { case user, live, sme }
enum AuthorKind: String, Codable, Equatable { case user, live, sme, system }
enum ApproverChannel: String, Codable, Equatable { case voice, chat }
enum Severity: String, Codable, Equatable { case warn = "WARN", halt = "HALT" }

enum BodyContentType: String, Codable, Equatable {
    case markdown = "text/markdown"
    case json = "application/json"
    case code = "text/code"
}

// MARK: - Chat bus

struct ChatMessage: Codable, Equatable, Identifiable {
    var channelId: String
    var authorId: String
    var authorKind: AuthorKind
    var body: String
    var bodyContentType: BodyContentType
    var mentions: [String]
    var replyToId: String?
    var messageId: String
    var ts: Int64
    var streaming: Bool

    var id: String { messageId }

    init(channelId: String, authorId: String, authorKind: AuthorKind, body: String,
         bodyContentType: BodyContentType = .markdown, mentions: [String] = [],
         replyToId: String? = nil, messageId: String, ts: Int64, streaming: Bool = false) {
        self.channelId = channelId; self.authorId = authorId; self.authorKind = authorKind
        self.body = body; self.bodyContentType = bodyContentType; self.mentions = mentions
        self.replyToId = replyToId; self.messageId = messageId; self.ts = ts; self.streaming = streaming
    }

    enum CodingKeys: String, CodingKey {
        case channelId, authorId, authorKind, body, bodyContentType, mentions, replyToId, messageId, ts, streaming
    }

    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        channelId = try c.decode(String.self, forKey: .channelId)
        authorId = try c.decode(String.self, forKey: .authorId)
        authorKind = try c.decode(AuthorKind.self, forKey: .authorKind)
        body = try c.decode(String.self, forKey: .body)
        bodyContentType = try c.decodeIfPresent(BodyContentType.self, forKey: .bodyContentType) ?? .markdown
        mentions = try c.decodeIfPresent([String].self, forKey: .mentions) ?? []
        replyToId = try c.decodeIfPresent(String.self, forKey: .replyToId)
        messageId = try c.decode(String.self, forKey: .messageId)
        ts = try c.decode(Int64.self, forKey: .ts)
        streaming = try c.decodeIfPresent(Bool.self, forKey: .streaming) ?? false
    }
}

struct ChannelUpdate: Codable, Equatable {
    var messageId: String
    var deltaText: String
    var done: Bool
    var ts: Int64

    enum CodingKeys: String, CodingKey { case messageId, deltaText, done, ts }
    init(messageId: String, deltaText: String, done: Bool = false, ts: Int64) {
        self.messageId = messageId; self.deltaText = deltaText; self.done = done; self.ts = ts
    }
    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        messageId = try c.decode(String.self, forKey: .messageId)
        deltaText = try c.decode(String.self, forKey: .deltaText)
        done = try c.decodeIfPresent(Bool.self, forKey: .done) ?? false
        ts = try c.decode(Int64.self, forKey: .ts)
    }
}

struct ChannelInfo: Codable, Equatable, Identifiable {
    var id: String
    var title: String
    var smeId: String?
    var icon: String?
    var alwaysVisible: Bool
    var unreadHint: Int

    enum CodingKeys: String, CodingKey { case id, title, smeId, icon, alwaysVisible, unreadHint }
    init(id: String, title: String, smeId: String? = nil, icon: String? = nil,
         alwaysVisible: Bool = false, unreadHint: Int = 0) {
        self.id = id; self.title = title; self.smeId = smeId; self.icon = icon
        self.alwaysVisible = alwaysVisible; self.unreadHint = unreadHint
    }
    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = try c.decode(String.self, forKey: .title)
        smeId = try c.decodeIfPresent(String.self, forKey: .smeId)
        icon = try c.decodeIfPresent(String.self, forKey: .icon)
        alwaysVisible = try c.decodeIfPresent(Bool.self, forKey: .alwaysVisible) ?? false
        unreadHint = try c.decodeIfPresent(Int.self, forKey: .unreadHint) ?? 0
    }
}

struct ChannelList: Codable, Equatable {
    var channels: [ChannelInfo]
}

struct ChannelHint: Codable, Equatable {
    var channelId: String
    var hint: String        // "focus" | "flash" | "demote" | "collapse"
    var reason: String
}

// MARK: - SME deliberation

struct EvidenceRef: Codable, Equatable {
    var kind: String        // "frame" | "scope_capture" | "datasheet" | "url" | "file"
    var uri: String
    var note: String?
}

struct ProposedAction: Codable, Equatable {
    var tool: String
    var argsJson: String
    var rationale: String
    var risk: Risk
}

struct SmeResponse: Codable, Equatable, Identifiable {
    var smeId: String
    var callId: String
    var confidence: Float
    var claim: String
    var rationale: String
    var evidence: [EvidenceRef]
    var proposedActions: [ProposedAction]
    var dissentsWith: [String]
    var ts: Int64

    var id: String { "\(smeId)-\(ts)" }

    enum CodingKeys: String, CodingKey {
        case smeId, callId, confidence, claim, rationale, evidence, proposedActions, dissentsWith, ts
    }
    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        smeId = try c.decode(String.self, forKey: .smeId)
        callId = try c.decode(String.self, forKey: .callId)
        confidence = try c.decode(Float.self, forKey: .confidence)
        claim = try c.decode(String.self, forKey: .claim)
        rationale = try c.decode(String.self, forKey: .rationale)
        evidence = try c.decodeIfPresent([EvidenceRef].self, forKey: .evidence) ?? []
        proposedActions = try c.decodeIfPresent([ProposedAction].self, forKey: .proposedActions) ?? []
        dissentsWith = try c.decodeIfPresent([String].self, forKey: .dissentsWith) ?? []
        ts = try c.decode(Int64.self, forKey: .ts)
    }
}

struct DissentPair: Codable, Equatable {
    var a: String
    var b: String
    var aClaim: String
    var bClaim: String
    var crux: String
}

struct DissentReport: Codable, Equatable {
    var callId: String
    var parties: [String]
    var axis: String
    var summary: String
    var pairwise: [DissentPair]
    var ts: Int64
}

struct MergedOpinion: Codable, Equatable {
    var headline: String
    var supportingSmes: [String]
    var openQuestions: [String]

    enum CodingKeys: String, CodingKey { case headline, supportingSmes, openQuestions }
    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        headline = try c.decode(String.self, forKey: .headline)
        supportingSmes = try c.decodeIfPresent([String].self, forKey: .supportingSmes) ?? []
        openQuestions = try c.decodeIfPresent([String].self, forKey: .openQuestions) ?? []
    }
}

struct SummonGuild: Codable, Equatable {
    var callId: String
    var topic: String
    var smes: [String]
    var contextRefs: [String]
    var deadlineMs: Int

    enum CodingKeys: String, CodingKey { case callId, topic, smes, contextRefs, deadlineMs }
    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        callId = try c.decode(String.self, forKey: .callId)
        topic = try c.decode(String.self, forKey: .topic)
        smes = try c.decodeIfPresent([String].self, forKey: .smes) ?? []
        contextRefs = try c.decodeIfPresent([String].self, forKey: .contextRefs) ?? []
        deadlineMs = try c.decodeIfPresent(Int.self, forKey: .deadlineMs) ?? 30_000
    }
}

struct CheckpointMarker: Codable, Equatable {
    var checkpointId: String
    var graphNodeName: String
    var ts: Int64
}

// MARK: - Safety

struct SafetyInterrupt: Codable, Equatable {
    var severity: Severity
    var reason: String
    var suggestedRecoverActions: [ProposedAction]
    var ts: Int64

    enum CodingKeys: String, CodingKey { case severity, reason, suggestedRecoverActions, ts }
    init(severity: Severity, reason: String, suggestedRecoverActions: [ProposedAction] = [], ts: Int64) {
        self.severity = severity; self.reason = reason
        self.suggestedRecoverActions = suggestedRecoverActions; self.ts = ts
    }
    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        severity = try c.decode(Severity.self, forKey: .severity)
        reason = try c.decode(String.self, forKey: .reason)
        suggestedRecoverActions = try c.decodeIfPresent([ProposedAction].self, forKey: .suggestedRecoverActions) ?? []
        ts = try c.decode(Int64.self, forKey: .ts)
    }
}

// MARK: - Confirmation card (carried inside ConfirmationRequest.actionCardJson)

struct ActionCard: Codable, Equatable {
    var title: String
    var bodyMarkdown: String
    var diffMarkdown: String?
    var risk: Risk
    var affirmLabel: String
    var denyLabel: String

    enum CodingKeys: String, CodingKey { case title, bodyMarkdown, diffMarkdown, risk, affirmLabel, denyLabel }
    init(title: String, bodyMarkdown: String, diffMarkdown: String? = nil, risk: Risk,
         affirmLabel: String = "Approve", denyLabel: String = "Hold") {
        self.title = title; self.bodyMarkdown = bodyMarkdown; self.diffMarkdown = diffMarkdown
        self.risk = risk; self.affirmLabel = affirmLabel; self.denyLabel = denyLabel
    }
    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        title = try c.decode(String.self, forKey: .title)
        bodyMarkdown = try c.decode(String.self, forKey: .bodyMarkdown)
        diffMarkdown = try c.decodeIfPresent(String.self, forKey: .diffMarkdown)
        risk = try c.decode(Risk.self, forKey: .risk)
        affirmLabel = try c.decodeIfPresent(String.self, forKey: .affirmLabel) ?? "Approve"
        denyLabel = try c.decodeIfPresent(String.self, forKey: .denyLabel) ?? "Hold"
    }

    /// Parse the JSON string carried in `ConfirmationRequest.actionCardJson`.
    static func from(json: String?) -> ActionCard? {
        guard let json, let data = json.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(ActionCard.self, from: data)
    }
}

// MARK: - Transport / control envelopes

struct ReplayDone: Codable, Equatable {
    var resumeTs: Int64
    var checkpointId: String?
}

struct BackpressureNotice: Codable, Equatable {
    var dropped: Int
    var sinceTs: Int64
}

struct ErrorEvent: Codable, Equatable {
    var code: String        // invalid_event | unknown_channel | auth_failed | rate_limited | protocol_mismatch | internal_error
    var message: String
    var causedByMessageId: String?
    var ts: Int64

    var isFatal: Bool { code == "auth_failed" || code == "protocol_mismatch" }
}

// MARK: - Typed chat cards (parsed from a ChatMessage with bodyContentType == .json)

enum ChatCard: Equatable {
    case smeResponse(SmeResponse)
    case dissentReport(DissentReport)
    case actionCard(ActionCard)
    case mergedOpinion(MergedOpinion)
    case safetyInterrupt(SafetyInterrupt)
    case toolResult(name: String, json: String)
    case unsupported(kind: String, json: String)

    /// Dispatch on the `kind` discriminator inside a JSON `ChatMessage.body`.
    static func parse(_ body: String) -> ChatCard? {
        guard let data = body.data(using: .utf8) else { return nil }
        let dec = JSONDecoder()
        guard let probe = try? dec.decode(KindProbe.self, from: data) else { return nil }
        switch probe.kind {
        case "SmeResponse":     return (try? dec.decode(SmeResponse.self, from: data)).map(ChatCard.smeResponse)
        case "DissentReport":   return (try? dec.decode(DissentReport.self, from: data)).map(ChatCard.dissentReport)
        case "ActionCard":      return (try? dec.decode(ActionCard.self, from: data)).map(ChatCard.actionCard)
        case "MergedOpinion":   return (try? dec.decode(MergedOpinion.self, from: data)).map(ChatCard.mergedOpinion)
        case "SafetyInterrupt": return (try? dec.decode(SafetyInterrupt.self, from: data)).map(ChatCard.safetyInterrupt)
        case "ToolResult":      return .toolResult(name: probe.name ?? "tool", json: body)
        default:                return .unsupported(kind: probe.kind, json: body)
        }
    }

    private struct KindProbe: Codable { let kind: String; let name: String? }

    /// Encode a payload to a JSON string with a `kind` discriminator injected,
    /// so a top-level event (SmeResponse, DissentReport, …) can be mirrored into
    /// a channel as a `ChatMessage(bodyContentType: .json)` card (specs/04 §3.1).
    static func wrapJSON<T: Encodable>(kind: String, _ value: T) -> String? {
        guard let data = try? JSONEncoder().encode(value),
              var obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return nil }
        obj["kind"] = kind
        guard let out = try? JSONSerialization.data(withJSONObject: obj) else { return nil }
        return String(decoding: out, as: UTF8.self)
    }
}
