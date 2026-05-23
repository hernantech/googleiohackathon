import AVFoundation
import Foundation

// 16 kHz mono Int16 LE; 20 ms = 320 samples = 640 bytes
private let kTargetSampleRate: Double = 16_000
private let kChunkSamples: Int = 320
private let kChunkBytes: Int = kChunkSamples * 2   // Int16

actor MicCapture {
    var chunks: AsyncStream<AudioInChunk> { _chunks }

    private let _chunks: AsyncStream<AudioInChunk>
    private let continuation: AsyncStream<AudioInChunk>.Continuation

    private let engine = AVAudioEngine()
    private var converter: AVAudioConverter?
    private var accumulator = Data()

    init() {
        (_chunks, continuation) = AsyncStream<AudioInChunk>.makeStream()
    }

    func start() async throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord,
                                options: [.defaultToSpeaker, .allowBluetooth])
        try session.setActive(true)

        let inputNode = engine.inputNode
        let hwFormat = inputNode.outputFormat(forBus: 0)

        guard let targetFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: kTargetSampleRate,
            channels: 1,
            interleaved: true
        ) else {
            throw MicCaptureError.formatUnavailable
        }

        guard let conv = AVAudioConverter(from: hwFormat, to: targetFormat) else {
            throw MicCaptureError.converterUnavailable
        }
        converter = conv

        // Request ~20 ms hardware frames to align tap cadence with chunk size.
        let tapFrames = AVAudioFrameCount(hwFormat.sampleRate * 0.02)
        inputNode.installTap(onBus: 0,
                             bufferSize: tapFrames,
                             format: hwFormat) { [weak self] buffer, _ in
            guard let self else { return }
            Task { await self.convert(buffer, to: targetFormat) }
        }

        engine.prepare()
        try engine.start()
    }

    func stop() async {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        continuation.finish()
        try? AVAudioSession.sharedInstance().setActive(false)
    }

    // MARK: - Private

    private func convert(_ src: AVAudioPCMBuffer, to targetFormat: AVAudioFormat) {
        guard let conv = converter else { return }

        let ratio = kTargetSampleRate / src.format.sampleRate
        let outFrames = AVAudioFrameCount(ceil(Double(src.frameLength) * ratio)) + 1

        guard let outBuf = AVAudioPCMBuffer(pcmFormat: targetFormat,
                                            frameCapacity: outFrames) else { return }
        var srcExhausted = false
        var err: NSError?
        let status = conv.convert(to: outBuf, error: &err) { _, outStatus in
            guard !srcExhausted else {
                outStatus.pointee = .noDataNow
                return nil
            }
            srcExhausted = true
            outStatus.pointee = .haveData
            return src
        }
        guard status != .error, let int16Ptr = outBuf.int16ChannelData else { return }

        let frameCount = Int(outBuf.frameLength)
        let raw = UnsafeRawPointer(int16Ptr[0])
        accumulator.append(raw.assumingMemoryBound(to: UInt8.self),
                           count: frameCount * 2)

        while accumulator.count >= kChunkBytes {
            let pcm = Data(accumulator.prefix(kChunkBytes))
            accumulator.removeFirst(kChunkBytes)
            let ts = Int64(Date().timeIntervalSince1970 * 1_000_000_000)
            continuation.yield(AudioInChunk(pcm: pcm, timestampNs: ts))
        }
    }
}

enum MicCaptureError: Error {
    case formatUnavailable
    case converterUnavailable
}
