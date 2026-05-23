import RealityKit
import ARKit
import Foundation
import simd

// World-locked outline for a single detected component.
// Each edge of the polygon becomes a thin box-segment child entity.
// No LowLevelMesh / generateLineStrip (unavailable on iOS 17).

@MainActor
final class ComponentOutline {

    let entity: ModelEntity
    private let component: DetectedComponent
    private var polygon: [SIMD3<Float>]
    private var isHighlighted = false

    init(component: DetectedComponent, polygon: [SIMD3<Float>]) {
        self.component = component
        self.polygon = polygon
        self.entity = ModelEntity()
        self.entity.name = "outline-\(component.id)"
        rebuildEdges()
    }

    // Replace polygon geometry. pose is unused for the box approach but kept
    // for callers that may supply it for future use.
    func update(polygon: [SIMD3<Float>], pose: Pose6dof?) async {
        self.polygon = polygon
        rebuildEdges()
    }

    func setHighlighted(_ on: Bool) {
        isHighlighted = on
        let mat = edgeMaterial(highlighted: on)
        for child in entity.children {
            if var model = child as? ModelEntity {
                model.model?.materials = [mat]
            }
        }
    }

    // MARK: - Private

    private func rebuildEdges() {
        entity.children.removeAll()
        guard polygon.count >= 2 else { return }
        let mat = edgeMaterial(highlighted: isHighlighted)
        let n = polygon.count
        for i in 0..<n {
            let a = polygon[i]
            let b = polygon[(i + 1) % n]
            let segment = makeSegment(from: a, to: b, material: mat)
            entity.addChild(segment)
        }
    }

    private func edgeMaterial(highlighted: Bool) -> UnlitMaterial {
        let base = PanelTheme.outlineColor(for: component)
        let color: UIColor
        if highlighted {
            color = UIColor(PanelTheme.highlighted(base))
        } else {
            color = UIColor(base)
        }
        return UnlitMaterial(color: color)
    }

    private func makeSegment(
        from a: SIMD3<Float>,
        to b: SIMD3<Float>,
        material: UnlitMaterial
    ) -> ModelEntity {
        let diff = b - a
        let length = simd_length(diff)
        guard length > 0.0001 else { return ModelEntity() }

        let thickness: Float = 0.002  // 2 mm
        let mesh = MeshResource.generateBox(size: SIMD3(length, thickness, thickness))
        let seg = ModelEntity(mesh: mesh, materials: [material])

        // Position at midpoint
        seg.position = (a + b) * 0.5

        // Orient along the edge
        let dir = normalize(diff)
        let xAxis = SIMD3<Float>(1, 0, 0)
        seg.orientation = simd_quatf(from: xAxis, to: dir)

        return seg
    }
}
