import XCTest
@testable import Forge

// WITHOUT BACKEND — reconnect backoff curve.
final class BackoffPolicyTests: XCTestCase {

    func testSequenceThenCap() {
        var b = BackoffPolicy()
        XCTAssertEqual(b.next(), 0.25)
        XCTAssertEqual(b.next(), 0.5)
        XCTAssertEqual(b.next(), 1.0)
        XCTAssertEqual(b.next(), 2.0)
        XCTAssertEqual(b.next(), 4.0)
        XCTAssertEqual(b.next(), 8.0)
        XCTAssertEqual(b.next(), 10.0)   // capped
        XCTAssertEqual(b.next(), 10.0)
    }

    func testResetRestartsSequence() {
        var b = BackoffPolicy()
        _ = b.next(); _ = b.next(); _ = b.next()
        b.reset()
        XCTAssertEqual(b.next(), 0.25)
    }
}
