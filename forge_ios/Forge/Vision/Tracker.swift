import Vision
import CoreVideo
import Foundation

// MARK: - Tracker

actor Tracker {
    init() {}

    /// Warp the last known polygons by the frame-to-frame optical flow between two frames.
    /// Falls back to returning the original polygons if registration fails.
    func warp(
        polygons: [String: [SIMD2<Float>]],
        from previous: CVPixelBuffer,
        to current: CVPixelBuffer
    ) async -> [String: [SIMD2<Float>]] {
        guard !polygons.isEmpty else { return polygons }

        let request = VNTranslationalImageRegistrationRequest(targetedCVPixelBuffer: previous)
        let handler = VNImageRequestHandler(cvPixelBuffer: current, options: [:])

        do {
            try handler.perform([request])
        } catch {
            return polygons
        }

        guard let observation = request.results?.first as? VNImageTranslationAlignmentObservation else {
            return polygons
        }

        let tx = Float(observation.alignmentTransform.tx)
        let ty = Float(observation.alignmentTransform.ty)

        // Skip warp if the estimated motion is negligible.
        guard tx != 0 || ty != 0 else { return polygons }

        let delta = SIMD2<Float>(tx, ty)
        var warped = [String: [SIMD2<Float>]](minimumCapacity: polygons.count)
        for (key, vertices) in polygons {
            warped[key] = vertices.map { $0 + delta }
        }
        return warped
    }
}
