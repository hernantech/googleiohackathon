import SwiftUI
import ARKit
import RealityKit
import simd
import os

// Day-0 canary. Validates the three load-bearing assumptions in one screen:
//   1. ARKit world tracking + LiDAR mesh hit-test produce sensible world points.
//   2. The orchestrator WS handshake works on the device's network.
//   3. RealityKit can mount a world-locked custom entity.
//
// Success criterion (per IMPLEMENTATION.md Phase 3): within ~5 s of launch the
// console (subsystem ai.forge.ios) prints "It works." AND one bright-green
// world-locked box appears at screen center.

struct SpikeView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var arView = ARView(frame: .zero)
    @State private var status = "Starting…"
    @State private var driver = SpikeDriver()

    var body: some View {
        ZStack {
            SpikeARContainer(arView: arView).ignoresSafeArea()
            VStack {
                HStack {
                    Button("Close") { dismiss() }.padding()
                    Spacer()
                }
                Spacer()
                Text(status)
                    .font(.system(.footnote, design: .monospaced))
                    .padding(10)
                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 8))
                    .padding(.bottom, 40)
            }
            // Center reticle.
            Circle().strokeBorder(.green, lineWidth: 2).frame(width: 24, height: 24)
        }
        .task { status = await driver.run(in: arView) }
    }
}

private struct SpikeARContainer: UIViewRepresentable {
    let arView: ARView
    func makeUIView(context: Context) -> ARView { arView }
    func updateUIView(_ uiView: ARView, context: Context) {}
}

@MainActor
final class SpikeDriver {
    private let log = Logger(subsystem: "ai.forge.ios", category: "Spike")
    private let arkit = ARKitSession()

    /// Runs the full canary and returns a short human-readable status line.
    func run(in arView: ARView) async -> String {
        // Drive the ARView from the shared, fully-configured ARKit session.
        arView.session = arkit.session
        do {
            try await arkit.start()
        } catch {
            log.error("[Spike] ARKit failed to start: \(error.localizedDescription, privacy: .public)")
            return "ARKit failed: \(error.localizedDescription)"
        }

        // (1) Intrinsics + first frame timestamps.
        if let intr = await firstIntrinsics() {
            log.info("[Spike] ARKit session started, intrinsics fx=\(intr.focalLengthPx.x, privacy: .public) cx=\(intr.principalPointPx.x, privacy: .public)")
        }

        // (2) WS handshake latency (time to reach .open).
        let socket = OrchestratorSocket(
            url: ConfigStore.load().orchestratorURL,
            authToken: ConfigStore.load().authToken,
            sessionId: UUID().uuidString
        )
        let started = Date()
        await socket.start()
        let ms = await waitForOpen(socket, since: started)
        let wsLine: String
        if let ms {
            log.info("[Spike] WS hello roundtrip: \(ms, privacy: .public)ms")
            wsLine = "WS \(ms)ms"
        } else {
            log.error("[Spike] WS unreachable")
            wsLine = "WS unreachable"
        }

        // (3) Raycast through screen center → world-locked green box.
        let worldLine = placeCenterOutline(in: arView)

        return "Spike: \(wsLine) · \(worldLine)"
    }

    private func firstIntrinsics() async -> CameraIntrinsics? {
        // Pull the first few frames to confirm the pump is alive.
        var seen = 0
        for await sample in await arkit.frames {
            seen += 1
            log.info("[Spike] frame \(seen, privacy: .public) ts=\(sample.timestampNs, privacy: .public)")
            if seen >= 5 { return sample.intrinsics }
        }
        return await arkit.intrinsics
    }

    private func waitForOpen(_ socket: OrchestratorSocket, since started: Date) async -> Int? {
        let deadline = Date().addingTimeInterval(3.0)
        for await state in await socket.state {
            if case .open = state {
                return Int(Date().timeIntervalSince(started) * 1000)
            }
            if Date() > deadline { return nil }
        }
        return nil
    }

    private func placeCenterOutline(in arView: ARView) -> String {
        let center = CGPoint(x: arView.bounds.midX, y: arView.bounds.midY)
        guard let result = arView.raycast(from: center, allowing: .estimatedPlane, alignment: .any).first else {
            log.notice("[Spike] no raycast hit yet — pan the phone across the bench and re-run")
            return "no world hit"
        }
        let t = result.worldTransform
        let p = SIMD3<Float>(t.columns.3.x, t.columns.3.y, t.columns.3.z)

        let anchor = AnchorEntity(world: t)
        let box = ModelEntity(
            mesh: .generateBox(size: 0.05),
            materials: [UnlitMaterial(color: .green)]
        )
        anchor.addChild(box)
        arView.scene.addAnchor(anchor)

        log.info("[Spike] It works. anchor=\(anchor.id, privacy: .public) world=(\(p.x, privacy: .public), \(p.y, privacy: .public), \(p.z, privacy: .public))")
        return String(format: "It works. (%.2f, %.2f, %.2f)", p.x, p.y, p.z)
    }
}
