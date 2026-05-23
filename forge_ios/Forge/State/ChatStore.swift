import Foundation
import Observation

// v2 Discord-style multi-channel chat bus (specs/04). Messages are the wire
// `ChatMessage` (Proto), keyed by `channelId`. Streaming deltas arrive as
// `ChannelUpdate` and append to an existing message body. Dedup is by
// `messageId` (ULID) so reconnect/replay is idempotent (specs/04 §5).

@Observable @MainActor
final class ChatStore {
    /// Server-defined channels (from `ChannelList`), render order preserved.
    private(set) var channels: [ChannelInfo] = []
    /// Messages per channel, in arrival (ts-monotonic) order.
    private(set) var messages: [String: [ChatMessage]] = [:]
    /// Channel currently shown in the UI.
    var selectedChannelId: String = "#general"
    /// Channels the user has muted locally (server still ships all).
    var muted: Set<String> = []

    private var seenMessageIds = Set<String>()
    private var indexByMessageId: [String: (channel: String, idx: Int)] = [:]

    var orderedChannels: [ChannelInfo] { channels }
    func messages(in channelId: String) -> [ChatMessage] { messages[channelId] ?? [] }
    func has(messageId: String) -> Bool { seenMessageIds.contains(messageId) }

    /// Apply a `ChannelList` envelope at connect/replay time.
    func setChannels(_ infos: [ChannelInfo]) {
        channels = infos
        for info in infos where messages[info.id] == nil { messages[info.id] = [] }
        if !infos.contains(where: { $0.id == selectedChannelId }), let first = infos.first {
            selectedChannelId = first.id
        }
    }

    /// Insert a wire `ChatMessage`; dedups by `messageId` (replace on repeat).
    func ingest(_ msg: ChatMessage) {
        if let loc = indexByMessageId[msg.messageId] {
            messages[loc.channel]?[loc.idx] = msg
            return
        }
        ensureChannel(msg.channelId)
        messages[msg.channelId, default: []].append(msg)
        let idx = (messages[msg.channelId]?.count ?? 1) - 1
        indexByMessageId[msg.messageId] = (msg.channelId, idx)
        seenMessageIds.insert(msg.messageId)
    }

    /// Append a streaming delta to an existing message. Returns false if the
    /// parent message is unknown (caller may buffer up to 2s then drop, §5).
    @discardableResult
    func apply(_ update: ChannelUpdate) -> Bool {
        guard let loc = indexByMessageId[update.messageId],
              var msg = messages[loc.channel]?[loc.idx] else { return false }
        msg.body += update.deltaText
        if update.done { msg.streaming = false }
        messages[loc.channel]?[loc.idx] = msg
        return true
    }

    /// Synthesize a local system message (e.g. "consulting the guild…").
    func system(_ text: String, channelId: String = "#general", ts: Int64) {
        ingest(ChatMessage(channelId: channelId, authorId: "@system", authorKind: .system,
                           body: text, messageId: UUID().uuidString, ts: ts))
    }

    func toggleMute(_ channelId: String) {
        if muted.contains(channelId) { muted.remove(channelId) } else { muted.insert(channelId) }
    }

    private func ensureChannel(_ id: String) {
        guard messages[id] == nil else { return }
        messages[id] = []
        if !channels.contains(where: { $0.id == id }) {
            let title = id.hasPrefix("#") ? String(id.dropFirst()).capitalized : id.capitalized
            channels.append(ChannelInfo(id: id, title: title,
                                        smeId: id.hasPrefix("#") ? "@\(id.dropFirst())" : nil))
        }
    }
}
