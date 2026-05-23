import SwiftUI
import UIKit
import AVFoundation
import Speech

// Gates the app behind the runtime permissions the AR + voice loop needs.
// Local Network has no request API — it is triggered implicitly by the first
// orchestrator connection, so it is not part of this gate.

@Observable @MainActor
final class PermissionModel {
    enum Status: Equatable { case unknown, granted, denied }

    var camera: Status = .unknown
    var microphone: Status = .unknown
    var speech: Status = .unknown

    var allGranted: Bool { camera == .granted && microphone == .granted && speech == .granted }
    var anyDenied: Bool { camera == .denied || microphone == .denied || speech == .denied }

    func requestAll() async {
        camera = await Self.requestCamera()
        microphone = await Self.requestMicrophone()
        speech = await Self.requestSpeech()
    }

    private static func requestCamera() async -> Status {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: return .granted
        case .notDetermined: return await AVCaptureDevice.requestAccess(for: .video) ? .granted : .denied
        default: return .denied
        }
    }

    private static func requestMicrophone() async -> Status {
        let app = AVAudioApplication.shared
        switch app.recordPermission {
        case .granted: return .granted
        case .undetermined:
            return await withCheckedContinuation { cont in
                AVAudioApplication.requestRecordPermission { ok in cont.resume(returning: ok ? .granted : .denied) }
            }
        default: return .denied
        }
    }

    private static func requestSpeech() async -> Status {
        switch SFSpeechRecognizer.authorizationStatus() {
        case .authorized: return .granted
        case .notDetermined:
            return await withCheckedContinuation { cont in
                SFSpeechRecognizer.requestAuthorization { s in cont.resume(returning: s == .authorized ? .granted : .denied) }
            }
        default: return .denied
        }
    }
}

/// Shows `content` once camera + mic + speech are granted; otherwise a request
/// prompt or a recovery screen that deep-links to Settings.
struct PermissionGate<Content: View>: View {
    @State private var model = PermissionModel()
    @ViewBuilder var content: () -> Content

    var body: some View {
        Group {
            if model.allGranted {
                content()
            } else if model.anyDenied {
                recovery
            } else {
                prompt
            }
        }
        .task { await model.requestAll() }
    }

    private var prompt: some View {
        VStack(spacing: 16) {
            ProgressView()
            Text("Requesting camera, microphone, and speech access…")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
        }
        .padding()
    }

    private var recovery: some View {
        VStack(spacing: 20) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 44))
                .foregroundStyle(.yellow)
            Text("Forge needs camera, microphone, and speech access")
                .font(.headline)
                .multilineTextAlignment(.center)
            Text("Enable them in Settings, then return to Forge.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button("Open Settings") {
                if let url = URL(string: UIApplication.openSettingsURLString) {
                    UIApplication.shared.open(url)
                }
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(32)
    }
}
