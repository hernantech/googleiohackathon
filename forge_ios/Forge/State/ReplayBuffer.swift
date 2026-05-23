import Foundation

struct ReplaySnapshot {
    let events: [AgentEvent]
    let frames: [FrameChunk]
}

actor ReplayBuffer {
    private let durationSec: Int

    // Each slot carries the wall-clock second it was recorded (as a Unix timestamp
    // truncated to seconds) so we can evict entries older than `durationSec`.
    private var eventSlots: [(ts: Int64, event: AgentEvent)] = []
    private var frameSlots: [(ts: Int64, frame: FrameChunk)] = []

    init(durationSec: Int = 30) {
        self.durationSec = durationSec
    }

    func record(event: AgentEvent) async {
        let now = currentSecond()
        eventSlots.append((ts: now, event: event))
        evict(now: now)
    }

    func record(frame: FrameChunk) async {
        let now = currentSecond()
        frameSlots.append((ts: now, frame: frame))
        evict(now: now)
    }

    func snapshot() async -> ReplaySnapshot {
        let now = currentSecond()
        let cutoff = now - Int64(durationSec)
        return ReplaySnapshot(
            events: eventSlots.filter { $0.ts >= cutoff }.map { $0.event },
            frames: frameSlots.filter { $0.ts >= cutoff }.map { $0.frame }
        )
    }

    // MARK: - Private

    private func currentSecond() -> Int64 {
        Int64(Date().timeIntervalSince1970)
    }

    private func evict(now: Int64) {
        let cutoff = now - Int64(durationSec)
        if let firstValid = eventSlots.firstIndex(where: { $0.ts >= cutoff }) {
            eventSlots.removeFirst(firstValid)
        }
        if let firstValid = frameSlots.firstIndex(where: { $0.ts >= cutoff }) {
            frameSlots.removeFirst(firstValid)
        }
    }
}
