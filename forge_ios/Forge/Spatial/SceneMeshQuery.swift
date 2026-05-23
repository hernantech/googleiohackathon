import ARKit
import simd
import Foundation

// Answers "what world point does this ray or pixel hit on the LiDAR scene mesh?"
// Primary path uses ARFrame.raycast(_:) (ARKit handles BVH internally).
// Fallback path manually tests the ray against each ARMeshAnchor face.

actor SceneMeshQuery {

    private let session: ARSession

    init(session: ARSession) {
        self.session = session
    }

    // MARK: - Public API

    /// Closest hit against the LiDAR scene mesh, or nil on miss.
    func hit(rayOrigin: SIMD3<Float>, rayDir: SIMD3<Float>) async -> SIMD3<Float>? {
        guard let frame = session.currentFrame else { return nil }
        return meshHit(rayOrigin: rayOrigin, rayDir: rayDir, frame: frame)
    }

    /// Full pipeline: image pixel (in camera-image pixel coords) → world point.
    /// Tries ARFrame.raycast first; falls back to manual mesh hit.
    func worldPoint(forPixel px: SIMD2<Float>, frame: ARFrameSample) async -> SIMD3<Float>? {
        guard let arFrame = session.currentFrame else { return nil }

        // Normalize pixel to [0,1] for world-direction computation.
        let imgSize = frame.intrinsics.imageSizePx
        let normalizedPoint = CGPoint(
            x: CGFloat(px.x) / CGFloat(imgSize.x),
            y: CGFloat(px.y) / CGFloat(imgSize.y)
        )

        // Primary: ARKit raycast (leverages internal LiDAR BVH + plane detectors).
        let worldDir = directionFromNormalizedPoint(normalizedPoint, camera: arFrame.camera)
        let cameraOrigin = arFrame.camera.transform.columns.3.xyz

        for target: ARRaycastQuery.Target in [.existingPlaneGeometry, .estimatedPlane] {
            let results = session.raycast(
                ARRaycastQuery(
                    origin: cameraOrigin,
                    direction: worldDir,
                    allowing: target,
                    alignment: .any
                )
            )
            if let first = results.first {
                return first.worldTransform.columns.3.xyz
            }
        }

        // Fallback: manual Möller–Trumbore mesh intersection.
        let camRay = Raycaster.pixelToCameraRay(pxX: px.x, pxY: px.y, intrinsics: frame.intrinsics)
        let worldRay = Raycaster.cameraRayToWorld(
            rayOriginCam: camRay.origin,
            rayDirCam: camRay.direction,
            cameraTransform: frame.cameraTransform
        )
        return meshHit(rayOrigin: worldRay.origin, rayDir: worldRay.direction, frame: arFrame)
    }

    // MARK: - Private helpers

    /// Builds a world-space direction from a normalized image point using ARCamera intrinsics.
    /// ARCamera.intrinsics is column-major: column 0 = (fx,0,0), column 1 = (0,fy,0), column 2 = (cx,cy,1).
    private func directionFromNormalizedPoint(_ point: CGPoint, camera: ARCamera) -> SIMD3<Float> {
        let intr = camera.intrinsics
        let fx = intr[0][0]; let fy = intr[1][1]
        let cx = intr[2][0]; let cy = intr[2][1]

        let imgSize = camera.imageResolution
        let pxX = Float(point.x) * Float(imgSize.width)
        let pxY = Float(point.y) * Float(imgSize.height)

        let camDir = simd_normalize(SIMD3<Float>((pxX - cx) / fx, -((pxY - cy) / fy), -1.0))
        let rot = simd_float3x3(
            camera.transform.columns.0.xyz,
            camera.transform.columns.1.xyz,
            camera.transform.columns.2.xyz
        )
        return simd_normalize(rot * camDir)
    }

    /// Iterates all ARMeshAnchor geometry faces and returns the closest ray intersection.
    private func meshHit(rayOrigin: SIMD3<Float>, rayDir: SIMD3<Float>, frame: ARFrame) -> SIMD3<Float>? {
        var closestT = Float.infinity
        var closestHit: SIMD3<Float>?

        for anchor in frame.anchors.compactMap({ $0 as? ARMeshAnchor }) {
            guard let (t, hit) = closestMeshHit(
                rayOrigin: rayOrigin,
                rayDir: rayDir,
                geometry: anchor.geometry,
                anchorTransform: anchor.transform
            ) else { continue }

            if t < closestT {
                closestT = t
                closestHit = hit
            }
        }
        return closestHit
    }

    /// Tests a ray against all triangles of one ARMeshGeometry.
    /// Returns (t, worldHitPoint) for the closest positive-t intersection, or nil.
    private func closestMeshHit(
        rayOrigin: SIMD3<Float>,
        rayDir: SIMD3<Float>,
        geometry: ARMeshGeometry,
        anchorTransform: simd_float4x4
    ) -> (Float, SIMD3<Float>)? {
        let vertices = geometry.vertices
        let faces = geometry.faces
        let vertexCount = vertices.count
        let faceCount = faces.count
        guard vertexCount > 0, faceCount > 0 else { return nil }

        // Read vertex positions respecting the source stride (typically 12 bytes for Float3).
        let vertexStride = vertices.stride
        let vertexBase = vertices.buffer.contents()
        var verts = [SIMD3<Float>](repeating: .zero, count: vertexCount)
        for i in 0 ..< vertexCount {
            let ptr = vertexBase.advanced(by: vertices.offset + i * vertexStride)
            verts[i] = ptr.load(as: SIMD3<Float>.self)
        }

        let indexBase = faces.buffer.contents()
        let bytesPerIndex = faces.bytesPerIndex

        var closestT = Float.infinity
        var closestHit: SIMD3<Float>?

        for f in 0 ..< faceCount {
            let i0 = readIndex(indexBase, face: f, vertex: 0, bytesPerIndex: bytesPerIndex)
            let i1 = readIndex(indexBase, face: f, vertex: 1, bytesPerIndex: bytesPerIndex)
            let i2 = readIndex(indexBase, face: f, vertex: 2, bytesPerIndex: bytesPerIndex)
            guard i0 < vertexCount, i1 < vertexCount, i2 < vertexCount else { continue }

            let v0 = (anchorTransform * SIMD4<Float>(verts[i0], 1)).xyz
            let v1 = (anchorTransform * SIMD4<Float>(verts[i1], 1)).xyz
            let v2 = (anchorTransform * SIMD4<Float>(verts[i2], 1)).xyz

            if let t = rayTriangleIntersect(origin: rayOrigin, dir: rayDir, v0: v0, v1: v1, v2: v2),
               t > 0, t < closestT {
                closestT = t
                closestHit = rayOrigin + rayDir * t
            }
        }
        return closestHit.map { (closestT, $0) }
    }

    private func readIndex(
        _ ptr: UnsafeMutableRawPointer,
        face: Int,
        vertex: Int,
        bytesPerIndex: Int
    ) -> Int {
        let byteOffset = (face * 3 + vertex) * bytesPerIndex
        if bytesPerIndex == 2 {
            return Int(ptr.load(fromByteOffset: byteOffset, as: UInt16.self))
        } else {
            return Int(ptr.load(fromByteOffset: byteOffset, as: UInt32.self))
        }
    }

    /// Möller–Trumbore ray/triangle intersection. Returns t along ray, or nil on miss.
    private func rayTriangleIntersect(
        origin: SIMD3<Float>,
        dir: SIMD3<Float>,
        v0: SIMD3<Float>,
        v1: SIMD3<Float>,
        v2: SIMD3<Float>
    ) -> Float? {
        let epsilon: Float = 1e-6
        let edge1 = v1 - v0
        let edge2 = v2 - v0
        let h = simd_cross(dir, edge2)
        let a = simd_dot(edge1, h)
        guard abs(a) > epsilon else { return nil }
        let f = 1.0 / a
        let s = origin - v0
        let u = f * simd_dot(s, h)
        guard u >= 0, u <= 1 else { return nil }
        let q = simd_cross(s, edge1)
        let v = f * simd_dot(dir, q)
        guard v >= 0, (u + v) <= 1 else { return nil }
        let t = f * simd_dot(edge2, q)
        guard t > epsilon else { return nil }
        return t
    }
}

// MARK: - SIMD helpers

private extension SIMD4 where Scalar == Float {
    var xyz: SIMD3<Float> { SIMD3(x, y, z) }
}
