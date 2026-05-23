import Foundation
import Observation

@Observable @MainActor
final class DetectionStore {
    var components: [DetectedComponent] = []
    var worldPolygons: [String: [SIMD3<Float>]] = [:]
    var focusedId: String?

    /// Replace the component list and rebuild world polygons.
    /// The `worldFor` closure is called synchronously — callers must pre-compute
    /// any async work (e.g. SceneMeshQuery hits) before invoking upsert.
    func upsert(
        _ result: LookAtBenchResult,
        worldFor: (Bbox2D, [SIMD2<Float>]?) -> [SIMD3<Float>]?
    ) {
        components = result.components

        var newPolygons = [String: [SIMD3<Float>]](minimumCapacity: result.components.count)
        for component in result.components {
            if let pts = worldFor(component.bbox, component.maskPolygon) {
                newPolygons[component.id] = pts
            }
        }
        worldPolygons = newPolygons

        // Drop focus if the focused component was not included in this result.
        if let fid = focusedId, !components.contains(where: { $0.id == fid }) {
            focusedId = nil
        }
    }
}
