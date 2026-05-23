import Vision
import CoreML
import CoreVideo
import Foundation

// MARK: - SegmentedInstance

struct SegmentedInstance {
    let id: Int
    let mask: CVPixelBuffer     // 1-channel uint8, 0 or 255
    let boundingBox: CGRect     // normalized 0..1
    let confidence: Float
}

// MARK: - Segmenter

actor Segmenter {
    init() {}

    func segment(_ buf: CVPixelBuffer) async throws -> [SegmentedInstance] {
        let request = VNGenerateForegroundInstanceMaskRequest()
        let handler = VNImageRequestHandler(cvPixelBuffer: buf, options: [:])

        try handler.perform([request])

        guard let result = request.results?.first else {
            return []
        }

        let allInstances = result.allInstances
        guard !allInstances.isEmpty else { return [] }

        var instances: [SegmentedInstance] = []
        instances.reserveCapacity(allInstances.count)

        for index in allInstances {
            let indexSet = IndexSet(integer: index)
            guard let scaledMask = try? result.generateScaledMaskForImage(
                forInstances: indexSet,
                from: handler
            ) else { continue }

            // Bounding box is not directly provided by the instance mask result;
            // derive it from the non-zero region of the mask.
            let boundingBox = normalizedBoundingBox(of: scaledMask)

            // VNGenerateForegroundInstanceMaskRequest does not expose per-instance
            // confidence directly; use 1.0 as a conservative default.
            instances.append(SegmentedInstance(
                id: index,
                mask: scaledMask,
                boundingBox: boundingBox,
                confidence: 1.0
            ))
        }

        return instances
    }

    // MARK: - Private helpers

    private func normalizedBoundingBox(of pixelBuffer: CVPixelBuffer) -> CGRect {
        CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }

        let width = CVPixelBufferGetWidth(pixelBuffer)
        let height = CVPixelBufferGetHeight(pixelBuffer)
        guard width > 0, height > 0,
              let base = CVPixelBufferGetBaseAddress(pixelBuffer) else {
            return CGRect(x: 0, y: 0, width: 1, height: 1)
        }

        let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
        let ptr = base.assumingMemoryBound(to: UInt8.self)

        var minX = width, maxX = 0, minY = height, maxY = 0

        for row in 0..<height {
            for col in 0..<width {
                if ptr[row * bytesPerRow + col] > 128 {
                    if col < minX { minX = col }
                    if col > maxX { maxX = col }
                    if row < minY { minY = row }
                    if row > maxY { maxY = row }
                }
            }
        }

        guard minX <= maxX, minY <= maxY else {
            return CGRect(x: 0, y: 0, width: 1, height: 1)
        }

        let fw = Float(width), fh = Float(height)
        return CGRect(
            x:      Double(Float(minX) / fw),
            y:      Double(Float(minY) / fh),
            width:  Double(Float(maxX - minX + 1) / fw),
            height: Double(Float(maxY - minY + 1) / fh)
        )
    }
}
