import Foundation

actor VoiceCommandRegistry {
    let actions: AsyncStream<InputAction>
    private let continuation: AsyncStream<InputAction>.Continuation

    // Keyword table: order matters — first match wins within a transcript.
    private static let rules: [(keywords: [String], intent: VoiceIntentKind)] = [
        (["look at", "what do you see"], .lookAtBench),
        (["focus", "select"], .focusComponent),
        (["dismiss", "close"], .dismissPanel),
        (["resume"], .resumeSession),
        (["pause"], .pauseSession),
        (["capture", "snapshot"], .capture),
    ]

    init() {
        var cont: AsyncStream<InputAction>.Continuation!
        actions = AsyncStream { cont = $0 }
        continuation = cont
    }

    func feed(_ transcript: String) {
        let lower = transcript.lowercased()
        for rule in Self.rules {
            guard rule.keywords.contains(where: { lower.contains($0) }) else { continue }
            continuation.yield(
                .voiceCommand(intent: rule.intent, rawText: transcript)
            )
            return
        }
        // No match — emit nothing.
    }
}
