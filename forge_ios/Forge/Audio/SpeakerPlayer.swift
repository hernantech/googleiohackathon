import AVFoundation
import Foundation

// Gemini output: 24 kHz mono signed-16-bit LE
private let kPlaybackSampleRate: Double = 24_000

actor SpeakerPlayer {
    private let engine = AVAudioPlayerNode()
    private let avEngine = AVAudioEngine()

    // Format for all decoded PCM buffers scheduled on the player node.
    private let playbackFormat: AVAudioFormat

    init() {
        // Force-unwrap is safe: these are constant well-formed parameters.
        playbackFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: kPlaybackSampleRate,
            channels: 1,
            interleaved: true
        )!
    }

    func start() async throws {
        let session = AVAudioSession.sharedInstance()
        // Merge with MicCapture's category if already active; set here as a
        // best-effort in case SpeakerPlayer is used standalone.
        try session.setCategory(.playAndRecord,
                                options: [.defaultToSpeaker, .allowBluetooth])
        try session.setActive(true)

        avEngine.attach(engine)
        avEngine.connect(engine,
                         to: avEngine.mainMixerNode,
                         format: playbackFormat)
        avEngine.prepare()
        try avEngine.start()
        engine.play()
    }

    func stop() async {
        engine.stop()
        avEngine.stop()
        try? AVAudioSession.sharedInstance().setActive(false)
    }

    // Decodes a base64-encoded 24 kHz mono LE16 PCM blob and schedules it.
    func enqueue(_ pcmBase64: String) async {
        guard let raw = Data(base64Encoded: pcmBase64) else { return }
        guard !raw.isEmpty, raw.count % 2 == 0 else { return }

        let frameCount = AVAudioFrameCount(raw.count / 2)
        guard let buf = AVAudioPCMBuffer(pcmFormat: playbackFormat,
                                         frameCapacity: frameCount) else { return }
        buf.frameLength = frameCount

        // Copy Int16 samples directly into the interleaved channel data pointer.
        raw.withUnsafeBytes { (src: UnsafeRawBufferPointer) in
            guard let int16Ptr = buf.int16ChannelData else { return }
            memcpy(int16Ptr[0], src.baseAddress!, raw.count)
        }

        engine.scheduleBuffer(buf, completionHandler: nil)
    }
}
