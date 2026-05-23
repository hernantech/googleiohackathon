import XCTest
import simd
@testable import Forge

// WITHOUT BACKEND — detection-store world-polygon mapping + focus retention.
@MainActor
final class DetectionStoreTests: XCTestCase {

    private func component(_ id: String) -> DetectedComponent {
        DetectedComponent(id: id, partNumber: "P-\(id)",
                          bbox: Bbox2D(x1: 0, y1: 0, x2: 10, y2: 10))
    }

    func testUpsertBuildsWorldPolygonsPerComponent() {
        let store = DetectionStore()
        let result = LookAtBenchResult(components: [component("U1"), component("R3")])
        store.upsert(result) { bbox, _ in
            [SIMD3<Float>(Float(bbox.x1), 0, 0)]   // any non-nil mapping
        }
        XCTAssertEqual(Set(store.components.map(\.id)), ["U1", "R3"])
        XCTAssertNotNil(store.worldPolygons["U1"])
        XCTAssertNotNil(store.worldPolygons["R3"])
    }

    func testUpsertSkipsComponentsWithNoWorldHit() {
        let store = DetectionStore()
        store.upsert(LookAtBenchResult(components: [component("U1")])) { _, _ in nil }
        XCTAssertEqual(store.components.count, 1)
        XCTAssertTrue(store.worldPolygons.isEmpty)   // no mesh hit → no polygon
    }

    func testFocusRetainedWhenStillPresentDroppedOtherwise() {
        let store = DetectionStore()
        store.upsert(LookAtBenchResult(components: [component("U1"), component("R3")])) { _, _ in [.zero] }
        store.focusedId = "U1"

        store.upsert(LookAtBenchResult(components: [component("U1")])) { _, _ in [.zero] }
        XCTAssertEqual(store.focusedId, "U1")          // still present → kept

        store.upsert(LookAtBenchResult(components: [component("R3")])) { _, _ in [.zero] }
        XCTAssertNil(store.focusedId)                   // gone → dropped
    }
}
