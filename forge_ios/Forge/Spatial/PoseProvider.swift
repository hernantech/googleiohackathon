import ARKit
import simd
import Foundation

// Polls ARSession.currentFrame at ~60 Hz and yields Pose6dof on each frame.
// The ARSession is OWNED by Camera/ARKitSession; we only read currentFrame.
// session.delegate is NOT set here.

actor PoseProvider {

    private let session: ARSession
    private let _poses: AsyncStream<Pose6dof>
    private let _continuation: AsyncStream<Pose6dof>.Continuation

    var poses: AsyncStream<Pose6dof> { _poses }

    init(session: ARSession) {
        self.session = session

        var cont: AsyncStream<Pose6dof>.Continuation!
        _poses = AsyncStream { cont = $0 }
        _continuation = cont

        // Capture locals to avoid referencing self before full init.
        let capturedSession = session
        let capturedCont = cont!
        Task {
            while !Task.isCancelled {
                if let frame = capturedSession.currentFrame {
                    let t = frame.camera.transform
                    let position = SIMD3<Float>(t.columns.3.x, t.columns.3.y, t.columns.3.z)
                    // simd_quatf(rotation:) extracts quaternion from the 3x3 rotation sub-matrix.
                    let orientation = simd_quatf(t)
                    let tsNs = Int64(frame.timestamp * 1_000_000_000)
                    capturedCont.yield(Pose6dof(positionM: position, orientationQuat: orientation, timestampNs: tsNs))
                }
                try? await Task.sleep(for: .milliseconds(16))   // ~62.5 Hz
            }
            capturedCont.finish()
        }
    }
}
