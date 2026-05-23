import ARKit
import AVFoundation
import CoreImage
import Foundation

// MARK: - Public frame sample

struct ARFrameSample {
    let pixelBuffer: CVPixelBuffer
    let cameraTransform: simd_float4x4
    let intrinsics: CameraIntrinsics
    let sceneDepth: ARDepthData?
    let timestampNs: Int64
}

// MARK: - Actor

actor ARKitSession {

    // Exposed so Spatial actors (AnchorRegistry, SceneMeshQuery, PoseProvider) can
    // share the exact same ARSession instance without going through this actor.
    nonisolated let _session = ARSession()
    nonisolated var session: ARSession { _session }

    var frames: AsyncStream<ARFrameSample> { _frames }
    var intrinsics: CameraIntrinsics? { _latestIntrinsics }

    // MARK: Private state

    private let _frames: AsyncStream<ARFrameSample>
    private let _framesContinuation: AsyncStream<ARFrameSample>.Continuation
    private let bridge: SessionDelegateBridge
    private var _latestSample: ARFrameSample?
    private var _latestIntrinsics: CameraIntrinsics?

    // MARK: Init

    init() {
        var cont: AsyncStream<ARFrameSample>.Continuation!
        _frames = AsyncStream { cont = $0 }
        _framesContinuation = cont

        bridge = SessionDelegateBridge()
        _session.delegate = bridge
    }

    // MARK: Lifecycle

    func start() async throws {
        let config = ARWorldTrackingConfiguration()
        config.planeDetection = [.horizontal, .vertical]
        if ARWorldTrackingConfiguration.supportsSceneReconstruction(.meshWithClassification) {
            config.sceneReconstruction = .meshWithClassification
        }
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) {
            config.frameSemantics.insert(.sceneDepth)
        }
        config.environmentTexturing = .none
        config.isAutoFocusEnabled = true

        // Wire bridge → actor before running the session.
        bridge.onFrame = { [weak self] frame in
            guard let self else { return }
            Task { await self.handleFrame(frame) }
        }

        _session.run(config, options: [.resetTracking, .removeExistingAnchors])
    }

    func stop() async {
        _session.pause()
        bridge.onFrame = nil
        _framesContinuation.finish()
    }

    // MARK: JPEG capture

    func captureLatestJPEG(quality: CGFloat) async -> FrameChunk? {
        guard let sample = _latestSample else { return nil }
        let buf = sample.pixelBuffer
        let width = CVPixelBufferGetWidth(buf)
        let height = CVPixelBufferGetHeight(buf)
        let data = FrameEncoder.encodeJPEG(buf, quality: quality)
        guard !data.isEmpty else { return nil }
        return FrameChunk(
            jpegBytes: data,
            widthPx: width,
            heightPx: height,
            timestampNs: sample.timestampNs
        )
    }

    // MARK: Internal frame handler

    private func handleFrame(_ frame: ARFrame) {
        let intr = IntrinsicsExtractor.extract(from: frame.camera)
        let tsNs = Int64(frame.timestamp * 1_000_000_000)
        let sample = ARFrameSample(
            pixelBuffer: frame.capturedImage,
            cameraTransform: frame.camera.transform,
            intrinsics: intr,
            sceneDepth: frame.sceneDepth,
            timestampNs: tsNs
        )
        _latestSample = sample
        _latestIntrinsics = intr
        _framesContinuation.yield(sample)
    }
}

// MARK: - Delegate bridge

// A plain NSObject that holds the ARSession delegate. Its only job is to forward
// session(_:didUpdate:) onto the actor. Spatial actors poll session.currentFrame
// directly and do not need the delegate.
private final class SessionDelegateBridge: NSObject, ARSessionDelegate {
    var onFrame: ((ARFrame) -> Void)?

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        onFrame?(frame)
    }
}
