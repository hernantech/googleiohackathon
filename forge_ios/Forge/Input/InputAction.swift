import Foundation
import simd

enum InputAction: Equatable {
    case tapComponent(id: String)
    case longPressAnchorReset(worldPoint: SIMD3<Float>)
    case pinchScalePanel(delta: Float)
    case voiceCommand(intent: VoiceIntentKind, rawText: String)
    case confirmationAccepted(callId: String)
    case confirmationRejected(callId: String)
}

enum VoiceIntentKind: String, Equatable {
    case lookAtBench
    case focusComponent
    case dismissPanel
    case pauseSession
    case resumeSession
    case capture
}
