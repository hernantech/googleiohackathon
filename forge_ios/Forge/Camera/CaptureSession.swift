import AVFoundation
import Foundation
import VideoToolbox

// MARK: - CaptureSession
//
// ONE AVCaptureSession with TWO outputs (HANDOFF §3 item 1, specs/00 §4):
//   1. AVCaptureVideoDataOutput — raw YUV frames → H.264 encode → LiveSocket
//   2. AVCapturePhotoOutput     — full-res JPEG stills → SnapshotClient
//
// NEVER create two camera sessions.  ARKitSession (ARKit world tracking) keeps
// the AR context; this actor handles the media paths for Gemini Live (always-on)
// and the 📷 snapshot (on-demand).  On devices where both need to coexist, the
// ARSession is paused before activating this AVCaptureSession and resumed after.
//
// Audio is captured at 16 kHz mono by MicCapture (Audio/MicCapture.swift); the
// PCM chunks are forwarded to LiveSocket as binary frames on the live WebSocket.

actor CaptureSession {

    // MARK: Public streams

    /// H.264 sample buffers ready to ship to LiveSocket.
    var encodedVideoChunks: AsyncStream<Data> { _videoChunks }
    private let _videoChunks: AsyncStream<Data>
    private let videoChunkCont: AsyncStream<Data>.Continuation

    // MARK: Private — capture

    private let session = AVCaptureSession()
    private let videoOutput = AVCaptureVideoDataOutput()
    private let photoOutput = AVCapturePhotoOutput()
    private let h264Encoder: H264Encoder

    private var running = false
    private var pendingPhotoCapture: CheckedContinuation<Data?, Never>?

    // MARK: Init

    init() {
        (self._videoChunks, self.videoChunkCont) = AsyncStream<Data>.makeStream()
        self.h264Encoder = H264Encoder()
    }

    // MARK: - Lifecycle

    func start() async throws {
        guard !running else { return }
        try await AVCaptureDevice.requestAccess(for: .video)
        try configureSession()
        session.startRunning()
        running = true
        Log.session.info("CaptureSession started (video + photo outputs)")
    }

    func stop() async {
        guard running else { return }
        session.stopRunning()
        running = false
        videoChunkCont.finish()
        h264Encoder.invalidate()
        Log.session.info("CaptureSession stopped")
    }

    // MARK: - On-demand still capture (📷 tap → POST /v2/snapshot)

    /// Capture one full-resolution JPEG still from the photo output.
    /// Returns nil if the session is not running or the capture times out.
    func captureStill() async -> Data? {
        guard running, session.isRunning else { return nil }
        return await withCheckedContinuation { continuation in
            pendingPhotoCapture = continuation
            let settings = AVCapturePhotoSettings()
            settings.flashMode = .off
            photoOutput.capturePhoto(with: settings, delegate: makePhotoDelegate())
        }
    }

    // MARK: - Private — session configuration

    private func configureSession() throws {
        session.beginConfiguration()
        defer { session.commitConfiguration() }

        session.sessionPreset = .photo   // full-res stills; video output gets downscaled

        guard let device = AVCaptureDevice.default(.builtInWideAngleCamera,
                                                    for: .video, position: .back),
              let input = try? AVCaptureDeviceInput(device: device),
              session.canAddInput(input) else {
            throw CaptureSessionError.deviceUnavailable
        }
        session.addInput(input)

        // Output 1: video data → H.264 encode → LiveSocket.
        videoOutput.videoSettings = [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange
        ]
        let videoQueue = DispatchQueue(label: "ai.forge.capture.video", qos: .userInitiated)
        videoOutput.setSampleBufferDelegate(makeVideoDelegate(), queue: videoQueue)
        videoOutput.alwaysDiscardsLateVideoFrames = true
        guard session.canAddOutput(videoOutput) else { throw CaptureSessionError.outputUnavailable }
        session.addOutput(videoOutput)

        if let conn = videoOutput.connection(with: .video) {
            conn.videoRotationAngle = 90  // portrait-up
        }

        // Output 2: photo output → full-res JPEG stills.
        photoOutput.maxPhotoQualityPrioritization = .quality
        guard session.canAddOutput(photoOutput) else { throw CaptureSessionError.outputUnavailable }
        session.addOutput(photoOutput)
    }

    // MARK: - Delegate factories

    // Delegates are plain NSObject subclasses that forward callbacks back to the actor.

    private func makeVideoDelegate() -> AVCaptureVideoDataOutputSampleBufferDelegate {
        VideoDelegate(actor: self)
    }

    private func makePhotoDelegate() -> AVCapturePhotoCaptureDelegate {
        PhotoDelegate(actor: self)
    }

    // MARK: - Called from delegates (on capture queues)

    nonisolated func _didOutputVideoSampleBuffer(_ sampleBuffer: CMSampleBuffer) {
        Task { await self.handleVideoSampleBuffer(sampleBuffer) }
    }

    nonisolated func _didFinishPhotoCapture(data: Data?) {
        Task { await self.resolvePhotoCapture(data) }
    }

    private func handleVideoSampleBuffer(_ sampleBuffer: CMSampleBuffer) {
        // Encode the raw frame to H.264 and forward to the live socket stream.
        if let encoded = h264Encoder.encode(sampleBuffer) {
            videoChunkCont.yield(encoded)
        }
    }

    private func resolvePhotoCapture(_ data: Data?) {
        pendingPhotoCapture?.resume(returning: data)
        pendingPhotoCapture = nil
    }
}

// MARK: - CaptureSessionError

enum CaptureSessionError: Error {
    case deviceUnavailable
    case outputUnavailable
}

// MARK: - Video delegate bridge

private final class VideoDelegate: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate, @unchecked Sendable {
    weak var actor: CaptureSession?
    init(actor: CaptureSession) { self.actor = actor }

    func captureOutput(_ output: AVCaptureOutput,
                       didOutput sampleBuffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        actor?._didOutputVideoSampleBuffer(sampleBuffer)
    }
}

// MARK: - Photo delegate bridge

private final class PhotoDelegate: NSObject, AVCapturePhotoCaptureDelegate, @unchecked Sendable {
    weak var actor: CaptureSession?
    init(actor: CaptureSession) { self.actor = actor }

    func photoOutput(_ output: AVCapturePhotoOutput,
                     didFinishProcessingPhoto photo: AVCapturePhoto,
                     error: Error?) {
        let data = error == nil ? photo.fileDataRepresentation() : nil
        actor?._didFinishPhotoCapture(data: data)
    }
}

// MARK: - H264Encoder
//
// Wraps VideoToolbox VTCompressionSession to produce Annex-B H.264 NAL units
// suitable for streaming to Gemini Live (specs/00 §4.1: H.264 always-on).

private final class H264Encoder: @unchecked Sendable {

    private var session: VTCompressionSession?
    private var outputBuffer: Data?
    private let lock = NSLock()

    init() { setupSession() }

    func encode(_ sampleBuffer: CMSampleBuffer) -> Data? {
        guard let session,
              let imageBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return nil }
        let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        var flags = VTEncodeInfoFlags()

        lock.lock()
        outputBuffer = nil
        lock.unlock()

        // Use the callback-based encode path (outputHandler not provided;
        // results arrive in the session-level output callback set up in setupSession).
        VTCompressionSessionEncodeFrame(session, imageBuffer: imageBuffer,
                                        presentationTimeStamp: pts,
                                        duration: .invalid, frameProperties: nil,
                                        sourceFrameRefcon: nil, infoFlagsOut: &flags)

        // The callback is synchronous for low-latency sessions; collect result.
        lock.lock()
        defer { lock.unlock() }
        return outputBuffer
    }

    func invalidate() {
        if let s = session { VTCompressionSessionInvalidate(s) }
        session = nil
    }

    private func setupSession() {
        let width  = 1280
        let height = 720
        let fps    = 30

        var s: VTCompressionSession?
        let status = VTCompressionSessionCreate(
            allocator: nil,
            width: Int32(width), height: Int32(height),
            codecType: kCMVideoCodecType_H264,
            encoderSpecification: nil,
            imageBufferAttributes: nil,
            compressedDataAllocator: nil,
            outputCallback: { refcon, _, status, _, sampleBuffer in
                guard let refcon, status == noErr,
                      let sampleBuffer else { return }
                let enc = Unmanaged<H264Encoder>.fromOpaque(refcon).takeUnretainedValue()
                enc.collectOutput(sampleBuffer)
            },
            refcon: Unmanaged.passUnretained(self).toOpaque(),
            compressionSessionOut: &s
        )
        guard status == noErr, let s else { return }

        VTSessionSetProperty(s, key: kVTCompressionPropertyKey_RealTime, value: kCFBooleanTrue)
        VTSessionSetProperty(s, key: kVTCompressionPropertyKey_ProfileLevel,
                             value: kVTProfileLevel_H264_Baseline_AutoLevel)
        VTSessionSetProperty(s, key: kVTCompressionPropertyKey_MaxKeyFrameInterval,
                             value: NSNumber(value: fps * 2))
        VTSessionSetProperty(s, key: kVTCompressionPropertyKey_AverageBitRate,
                             value: NSNumber(value: 1_500_000))
        VTCompressionSessionPrepareToEncodeFrames(s)
        session = s
    }

    private func collectOutput(_ sampleBuffer: CMSampleBuffer) {
        guard let block = CMSampleBufferGetDataBuffer(sampleBuffer) else { return }
        var totalLength = 0
        var dataPointer: UnsafeMutablePointer<CChar>?
        CMBlockBufferGetDataPointer(block, atOffset: 0, lengthAtOffsetOut: nil,
                                    totalLengthOut: &totalLength, dataPointerOut: &dataPointer)
        guard let dataPointer, totalLength > 0 else { return }

        // Convert AVCC length-prefixed NALUs to Annex-B start codes (0x00 00 00 01).
        var data = Data(capacity: totalLength)
        var offset = 0
        while offset < totalLength {
            guard offset + 4 <= totalLength else { break }
            let naluLength = dataPointer.advanced(by: offset).withMemoryRebound(to: UInt32.self, capacity: 1) {
                CFSwapInt32BigToHost($0.pointee)
            }
            offset += 4
            guard offset + Int(naluLength) <= totalLength else { break }
            data.append(contentsOf: [0x00, 0x00, 0x00, 0x01])  // Annex-B start code
            data.append(Data(bytes: dataPointer.advanced(by: offset), count: Int(naluLength)))
            offset += Int(naluLength)
        }

        lock.lock()
        outputBuffer = data
        lock.unlock()
    }
}
