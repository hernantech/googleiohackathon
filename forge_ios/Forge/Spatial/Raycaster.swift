import ARKit
import simd
import Foundation

// Pure math — no ARKit state. Converts image-space pixels to camera-space and
// then world-space rays using camera intrinsics and the 4x4 camera transform.

enum Raycaster {

    /// Builds a normalized ray in camera space from a pixel coordinate.
    /// Camera space: +X right, +Y up, -Z into scene (OpenCV convention with Y flipped).
    static func pixelToCameraRay(
        pxX: Float,
        pxY: Float,
        intrinsics: CameraIntrinsics
    ) -> (origin: SIMD3<Float>, direction: SIMD3<Float>) {
        let fx = intrinsics.focalLengthPx.x
        let fy = intrinsics.focalLengthPx.y
        let cx = intrinsics.principalPointPx.x
        let cy = intrinsics.principalPointPx.y

        let dir = simd_normalize(SIMD3<Float>(
            (pxX - cx) / fx,
            -((pxY - cy) / fy),   // negate Y: image row increases downward, camera Y increases upward
            -1.0
        ))
        return (origin: .zero, direction: dir)
    }

    /// Transforms a camera-space ray into world space using the ARKit 4x4 camera transform.
    /// Direction is rotated by the upper-left 3x3 (no translation); origin is full-point transform.
    static func cameraRayToWorld(
        rayOriginCam: SIMD3<Float>,
        rayDirCam: SIMD3<Float>,
        cameraTransform: simd_float4x4
    ) -> (origin: SIMD3<Float>, direction: SIMD3<Float>) {
        let rot = simd_float3x3(
            cameraTransform.columns.0.xyz,
            cameraTransform.columns.1.xyz,
            cameraTransform.columns.2.xyz
        )
        let worldOrigin = (cameraTransform * SIMD4<Float>(rayOriginCam, 1.0)).xyz
        let worldDir = simd_normalize(rot * rayDirCam)
        return (origin: worldOrigin, direction: worldDir)
    }
}

// MARK: - SIMD helpers

private extension SIMD4 where Scalar == Float {
    var xyz: SIMD3<Float> { SIMD3(x, y, z) }
}
