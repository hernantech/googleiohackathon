import AVFoundation
import Foundation
import Speech

actor VoiceIntent {
    var transcripts: AsyncStream<String> { _transcripts }

    private let _transcripts: AsyncStream<String>
    private let continuation: AsyncStream<String>.Continuation

    private let recognizer: SFSpeechRecognizer?
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?

    // Driven by MicCapture's tap via the shared AVAudioEngine input node.
    private let audioEngine = AVAudioEngine()
    private var isActive = false

    init() {
        (_transcripts, continuation) = AsyncStream<String>.makeStream()
        recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    }

    // MARK: - Public

    func setPushToTalk(_ on: Bool) async {
        if on {
            await startRecognitionIfNeeded()
        } else {
            stopRecognition()
        }
    }

    // MARK: - Private

    private func startRecognitionIfNeeded() async {
        guard !isActive else { return }
        guard let recognizer, recognizer.isAvailable else { return }

        // Request authorization on first use.
        let authorized = await withCheckedContinuation { cont in
            SFSpeechRecognizer.requestAuthorization { status in
                cont.resume(returning: status == .authorized)
            }
        }
        guard authorized else { return }

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        // Prefer on-device recognition when available (iOS 17+).
        if #available(iOS 17.0, *) {
            request.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition
        }
        recognitionRequest = request

        let inputNode = audioEngine.inputNode
        let fmt = inputNode.outputFormat(forBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: fmt) { [weak self] buf, _ in
            self?.recognitionRequest?.append(buf)
        }

        recognitionTask = recognizer.recognitionTask(with: request) { [weak self] result, error in
            guard let self else { return }
            if let result {
                let text = result.bestTranscription.formattedString
                Task { await self.yieldTranscript(text) }
            }
            if error != nil || (result?.isFinal == true) {
                Task { await self.stopRecognition() }
            }
        }

        audioEngine.prepare()
        try? audioEngine.start()
        isActive = true
    }

    private func stopRecognition() {
        guard isActive else { return }
        audioEngine.inputNode.removeTap(onBus: 0)
        audioEngine.stop()
        recognitionRequest?.endAudio()
        recognitionRequest = nil
        recognitionTask?.cancel()
        recognitionTask = nil
        isActive = false
    }

    private func yieldTranscript(_ text: String) {
        continuation.yield(text)
    }
}
