import Vision
import CoreVideo
import Accelerate
import Foundation

// MARK: - MaskPolygonizer

enum MaskPolygonizer {
    /// Extract the largest external contour from a 1-channel uint8 `CVPixelBuffer`
    /// (threshold 128) using a marching-squares approach, returning up to `maxVertices`
    /// normalized SIMD2<Float> points in 0..1 space.
    static func polygon(from mask: CVPixelBuffer, maxVertices: Int) -> [SIMD2<Float>] {
        CVPixelBufferLockBaseAddress(mask, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(mask, .readOnly) }

        let width  = CVPixelBufferGetWidth(mask)
        let height = CVPixelBufferGetHeight(mask)
        guard width > 1, height > 1,
              let base = CVPixelBufferGetBaseAddress(mask) else { return [] }

        let bytesPerRow = CVPixelBufferGetBytesPerRow(mask)
        let src = base.assumingMemoryBound(to: UInt8.self)

        // Build a boolean grid where true == foreground (>= 128).
        // Dims: (height+1) x (width+1) — padded by one pixel on each edge so
        // marching-squares cells never read out of bounds.
        let gridW = width  + 1
        let gridH = height + 1
        var grid = [Bool](repeating: false, count: gridW * gridH)
        for row in 0..<height {
            for col in 0..<width {
                grid[(row + 1) * gridW + (col + 1)] = src[row * bytesPerRow + col] >= 128
            }
        }

        // Collect all contour segments produced by marching squares.
        // Each cell produces 0, 1, or 2 line segments.
        var segments: [(SIMD2<Float>, SIMD2<Float>)] = []
        for row in 0..<height {
            for col in 0..<width {
                let tl = grid[ row      * gridW + col    ]
                let tr = grid[ row      * gridW + col + 1]
                let bl = grid[(row + 1) * gridW + col    ]
                let br = grid[(row + 1) * gridW + col + 1]

                let index = (tl ? 8 : 0) | (tr ? 4 : 0) | (br ? 2 : 0) | (bl ? 1 : 0)

                // Mid-points of the four cell edges (in normalized coordinates).
                let fW = Float(width), fH = Float(height)
                let x  = Float(col)   / fW
                let y  = Float(row)   / fH
                let dx = 1.0 / fW
                let dy = 1.0 / fH

                let top    = SIMD2<Float>(x + dx * 0.5, y)
                let right  = SIMD2<Float>(x + dx,       y + dy * 0.5)
                let bottom = SIMD2<Float>(x + dx * 0.5, y + dy)
                let left   = SIMD2<Float>(x,             y + dy * 0.5)

                switch index {
                case 1:  segments.append((bottom, left))
                case 2:  segments.append((right, bottom))
                case 3:  segments.append((right, left))
                case 4:  segments.append((top, right))
                case 5:
                    segments.append((top, left))
                    segments.append((right, bottom))
                case 6:  segments.append((top, bottom))
                case 7:  segments.append((top, left))
                case 8:  segments.append((left, top))
                case 9:  segments.append((bottom, top))
                case 10:
                    segments.append((left, bottom))
                    segments.append((top, right))
                case 11: segments.append((right, top))
                case 12: segments.append((left, right))
                case 13: segments.append((bottom, right))
                case 14: segments.append((left, bottom))
                default: break  // 0 and 15 are fully outside/inside
                }
            }
        }

        guard !segments.isEmpty else { return [] }

        // Chain segments into the longest closed polyline.
        let contour = chainLargestContour(from: segments)

        guard !contour.isEmpty else { return [] }

        // Uniform stride downsample to at most maxVertices.
        guard maxVertices > 0 else { return [] }
        if contour.count <= maxVertices { return contour }
        let stride = contour.count / maxVertices
        return (0..<maxVertices).map { contour[$0 * stride] }
    }

    // MARK: - Private

    private static func chainLargestContour(
        from segments: [(SIMD2<Float>, SIMD2<Float>)]
    ) -> [SIMD2<Float>] {
        // Build adjacency list keyed on quantized vertex positions so we can
        // walk connected chains without an epsilon search.
        let scale: Float = 4096
        func key(_ p: SIMD2<Float>) -> Int64 {
            let ix = Int64(p.x * scale)
            let iy = Int64(p.y * scale)
            return (iy << 20) | (ix & 0xFFFFF)
        }

        // Map each vertex key -> array of segment indices.
        var adjacency = [Int64: [Int]](minimumCapacity: segments.count * 2)
        for (i, seg) in segments.enumerated() {
            adjacency[key(seg.0), default: []].append(i)
            adjacency[key(seg.1), default: []].append(i)
        }

        var visited = [Bool](repeating: false, count: segments.count)
        var bestChain = [SIMD2<Float>]()

        for startIdx in 0..<segments.count {
            guard !visited[startIdx] else { continue }

            var chain = [SIMD2<Float>]()
            var current = segments[startIdx].0
            var segIdx  = startIdx

            while true {
                visited[segIdx] = true
                let seg = segments[segIdx]
                // Add the "other" endpoint.
                let next = key(seg.0) == key(current) ? seg.1 : seg.0
                chain.append(current)
                current = next

                // Find the unvisited neighbour sharing `current`.
                guard let neighbours = adjacency[key(current)] else { break }
                if let nextSeg = neighbours.first(where: { !visited[$0] }) {
                    segIdx = nextSeg
                } else {
                    break
                }
            }

            if chain.count > bestChain.count {
                bestChain = chain
            }
        }

        return bestChain
    }
}
