import Foundation

struct TimeoutError: Error {}

/// Polls `cond` until it returns true or the timeout elapses (then throws).
/// Used instead of fixed sleeps so async tests are fast and non-flaky.
func waitUntil(timeout: TimeInterval = 3, _ cond: () -> Bool) async throws {
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
        if cond() { return }
        try await Task.sleep(nanoseconds: 25_000_000)
    }
    if cond() { return }
    throw TimeoutError()
}

/// Thread-safe collector for draining an `AsyncStream` from a background Task.
final class Box<T>: @unchecked Sendable {
    private var items: [T] = []
    private let lock = NSLock()
    func add(_ x: T) { lock.lock(); items.append(x); lock.unlock() }
    func all() -> [T] { lock.lock(); defer { lock.unlock() }; return items }
}
