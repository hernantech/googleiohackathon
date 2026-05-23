import XCTest
@testable import Forge

// WITHOUT BACKEND — channel chat-store logic (specs/04): ingest, messageId
// dedup, streaming delta append, channel auto-creation, mute.
@MainActor
final class ChatStoreTests: XCTestCase {

    private func msg(_ id: String, channel: String = "#general", body: String = "hi", streaming: Bool = false) -> ChatMessage {
        ChatMessage(channelId: channel, authorId: "@u", authorKind: .user, body: body,
                    messageId: id, ts: 1, streaming: streaming)
    }

    func testSetChannelsSeedsBuckets() {
        let store = ChatStore()
        store.setChannels([ChannelInfo(id: "#a", title: "A"), ChannelInfo(id: "#b", title: "B")])
        XCTAssertEqual(store.channels.map(\.id), ["#a", "#b"])
        XCTAssertEqual(store.messages(in: "#a"), [])
        XCTAssertEqual(store.selectedChannelId, "#a")   // first becomes selected
    }

    func testIngestDedupsByMessageId() {
        let store = ChatStore()
        store.ingest(msg("m1", body: "first"))
        store.ingest(msg("m1", body: "edited"))   // same id → replace, not append
        store.ingest(msg("m2"))
        XCTAssertEqual(store.messages(in: "#general").count, 2)
        XCTAssertEqual(store.messages(in: "#general").first?.body, "edited")
        XCTAssertTrue(store.has(messageId: "m1"))
    }

    func testChannelUpdateAppendsStreamingDelta() {
        let store = ChatStore()
        store.ingest(msg("m1", body: "Hel", streaming: true))
        XCTAssertTrue(store.apply(ChannelUpdate(messageId: "m1", deltaText: "lo", done: false, ts: 2)))
        XCTAssertEqual(store.messages(in: "#general").first?.body, "Hello")
        XCTAssertTrue(store.messages(in: "#general").first?.streaming ?? false)

        store.apply(ChannelUpdate(messageId: "m1", deltaText: "!", done: true, ts: 3))
        XCTAssertEqual(store.messages(in: "#general").first?.body, "Hello!")
        XCTAssertFalse(store.messages(in: "#general").first?.streaming ?? true)
    }

    func testOrphanUpdateReturnsFalse() {
        let store = ChatStore()
        XCTAssertFalse(store.apply(ChannelUpdate(messageId: "ghost", deltaText: "x", done: false, ts: 1)))
    }

    func testIngestAutoCreatesUnknownChannel() {
        let store = ChatStore()
        store.ingest(msg("m1", channel: "#power"))
        XCTAssertTrue(store.channels.contains { $0.id == "#power" })
        XCTAssertEqual(store.messages(in: "#power").count, 1)
    }

    func testMuteToggles() {
        let store = ChatStore()
        store.toggleMute("#dissent")
        XCTAssertTrue(store.muted.contains("#dissent"))
        store.toggleMute("#dissent")
        XCTAssertFalse(store.muted.contains("#dissent"))
    }
}
