import ARKit
import Foundation

enum IntrinsicsExtractor {
    // ARCamera.intrinsics is a column-major 3x3 float matrix:
    //   K = [ fx  0  cx ]
    //       [  0 fy  cy ]
    //       [  0  0   1 ]
    // In simd_float3x3, columns are accessed as K[col][row], so:
    //   fx = K[0][0], fy = K[1][1], cx = K[2][0], cy = K[2][1]
    static func extract(from camera: ARCamera) -> CameraIntrinsics {
        let K = camera.intrinsics
        let size = camera.imageResolution
        return CameraIntrinsics(
            focalLengthPx: SIMD2<Float>(K[0][0], K[1][1]),
            principalPointPx: SIMD2<Float>(K[2][0], K[2][1]),
            distortionCoeffs: [],
            imageSizePx: SIMD2<Int32>(Int32(size.width), Int32(size.height))
        )
    }
}
