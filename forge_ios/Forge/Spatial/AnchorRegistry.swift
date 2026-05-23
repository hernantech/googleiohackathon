import ARKit
import simd
import Foundation

// Maintains a stable id → ARAnchor mapping for the lifetime of the session.
// Callers use string IDs (e.g. component IDs from the orchestrator); the actor
// maps them to ARAnchors whose world transforms are kept up-to-date.

actor AnchorRegistry {

    private let session: ARSession
    // Maps caller-supplied id → the live ARAnchor in the session.
    private var registry: [String: ARAnchor] = [:]

    // Positions that differ by less than this threshold do not trigger a
    // remove + re-add cycle (avoids anchor flicker for sub-mm jitter).
    private let moveThresholdM: Float = 0.005

    init(session: ARSession) {
        self.session = session
    }

    // MARK: - Public API

    /// Returns the existing anchor if the position hasn't moved meaningfully;
    /// otherwise removes the old one and adds a new one at the updated position.
    /// Always returns an anchor with the same identifier for repeated calls with
    /// the same id as long as the position is stable.
    func registerOrUpdate(id: String, worldPosition: SIMD3<Float>) async -> ARAnchor {
        if let existing = registry[id] {
            let existingPos = existing.transform.columns.3.xyz
            if simd_distance(existingPos, worldPosition) < moveThresholdM {
                return existing
            }
            session.remove(anchor: existing)
        }
        var transform = matrix_identity_float4x4
        transform.columns.3 = SIMD4<Float>(worldPosition, 1.0)
        let anchor = ARAnchor(transform: transform)
        session.add(anchor: anchor)
        registry[id] = anchor
        return anchor
    }

    func remove(id: String) async {
        guard let anchor = registry.removeValue(forKey: id) else { return }
        session.remove(anchor: anchor)
    }

    func anchor(forId id: String) async -> ARAnchor? {
        registry[id]
    }

    var allIds: [String] {
        Array(registry.keys)
    }
}

// MARK: - SIMD helpers

private extension SIMD4 where Scalar == Float {
    var xyz: SIMD3<Float> { SIMD3(x, y, z) }
}
