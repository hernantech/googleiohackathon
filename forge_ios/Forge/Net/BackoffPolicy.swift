import Foundation

// Exponential backoff with a fixed step sequence, capped at 10 s.
struct BackoffPolicy {
    // Durations in seconds: 0.25, 0.5, 1, 2, 4, 8, then cap at 10.
    private static let steps: [TimeInterval] = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0]
    private static let cap: TimeInterval = 10.0

    private var attempt: Int = 0

    /// Delay for the current attempt, then advance the counter.
    mutating func next() -> TimeInterval {
        let delay = attempt < Self.steps.count
            ? Self.steps[attempt]
            : Self.cap
        attempt += 1
        return delay
    }

    mutating func reset() {
        attempt = 0
    }
}
