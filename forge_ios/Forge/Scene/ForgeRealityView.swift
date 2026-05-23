import SwiftUI
import ARKit
import RealityKit
import Foundation

// MARK: - ForgeRealityView

/// Root rendering surface. Hosts the AR camera via ARView (iOS 17) and layers
/// all SwiftUI chrome in a ZStack on top.
struct ForgeRealityView: View {

    @Environment(SessionViewModel.self) private var vm

    @State private var arViewRef: ARView? = nil
    @State private var showChat = false
    @State private var showSettings = false

    var body: some View {
        ZStack {
            arLayer
            overlayLayer
            SafetyInterruptView()
                .environment(vm)
        }
        .ignoresSafeArea()
        .sheet(isPresented: $showSettings) {
            SettingsPanel()
        }
        .sheet(item: pendingConfirmationBinding) { item in
            ConfirmationSheet(confirmation: item.confirmation)
                .environment(vm)
        }
    }

    // MARK: - Bindings

    private var pendingConfirmationBinding: Binding<ConfirmationItem?> {
        Binding(
            get: { vm.pendingConfirmation.map { ConfirmationItem(confirmation: $0) } },
            set: { _ in }
        )
    }

    // MARK: - AR camera layer

    @ViewBuilder
    private var arLayer: some View {
        if let session = vm.arSession {
            ARViewBridge(arSession: session, arViewOut: $arViewRef)
                .ignoresSafeArea()
        } else {
            Color.black.ignoresSafeArea()
        }
    }

    // MARK: - SwiftUI chrome overlay

    @ViewBuilder
    private var overlayLayer: some View {
        VStack {
            topBar
            Spacer()
            bottomBar
        }
        .overlay(alignment: .top) {
            ToastStack()
                .environment(vm)
                .padding(.top, 90)
        }
        .overlay {
            if let arView = arViewRef {
                ScreenSpaceLabelOverlay(arView: arView, vm: vm)
            }
        }
    }

    private var topBar: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                HudOverlay(status: vm.hudStatus)
                if case .degraded(let reason) = vm.connection {
                    DegradedStatusPanel(reason: reason)
                } else if case .closed = vm.connection {
                    DegradedStatusPanel(reason: "disconnected")
                }
            }
            Spacer()
            Button { showSettings = true } label: {
                Image(systemName: "gear")
                    .foregroundStyle(PanelTheme.primaryText)
                    .padding(PanelTheme.hudPadding)
            }
        }
        .padding(.horizontal, PanelTheme.hudPadding)
        .padding(.top, PanelTheme.hudPadding)
    }

    private var bottomBar: some View {
        HStack(alignment: .bottom) {
            Spacer()
            if showChat {
                ExpertChatPanel()
                    .frame(width: PanelTheme.panelWidth)
                    .transition(.move(edge: .trailing))
            }
            Button {
                withAnimation(.easeInOut(duration: 0.2)) { showChat.toggle() }
            } label: {
                Image(systemName: showChat ? "bubble.right.fill" : "bubble.right")
                    .foregroundStyle(PanelTheme.primaryText)
                    .padding(PanelTheme.hudPadding)
                    .background(PanelTheme.panelBackground)
                    .clipShape(Circle())
            }
            .padding(.bottom, 24)
            .padding(.trailing, 12)
        }
    }
}

// MARK: - ARView bridge (UIViewRepresentable)

/// Wraps an ARView and publishes the live ARView reference back to @State via
/// a @Binding so the label overlay can call arView.project().
private struct ARViewBridge: UIViewRepresentable {

    let arSession: ARSession
    @Binding var arViewOut: ARView?

    final class Coordinator {
        let arView: ARView
        init(session: ARSession) {
            arView = ARView(frame: .zero)
            arView.session = session
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(session: arSession) }

    func makeUIView(context: Context) -> ARView { context.coordinator.arView }

    func updateUIView(_ uiView: ARView, context: Context) {
        // Publish the reference back without triggering a re-render cycle.
        if arViewOut !== uiView {
            DispatchQueue.main.async { self.arViewOut = uiView }
        }
    }
}

// MARK: - Screen-space label overlay

/// At ~30 Hz, projects each detection's world-polygon centroid into screen
/// space and places a ComponentLabel at that position.
private struct ScreenSpaceLabelOverlay: View {

    let arView: ARView
    let vm: SessionViewModel

    @StateObject private var ticker = LabelTicker()

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .topLeading) {
                // Reading ticker.tick causes a redraw on each timer fire.
                let _ = ticker.tick
                ForEach(vm.detections.components, id: \.id) { component in
                    if let pts = vm.detections.worldPolygons[component.id],
                       let centroid = centroid(of: pts),
                       let screen = arView.project(centroid),
                       geo.frame(in: .local).contains(screen) {
                        ComponentLabel(
                            component: component,
                            isFocused: vm.detections.focusedId == component.id,
                            onTap: { vm.send(.tapComponent(id: component.id)) }
                        )
                        .position(x: screen.x, y: screen.y)
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func centroid(of pts: [SIMD3<Float>]) -> SIMD3<Float>? {
        guard !pts.isEmpty else { return nil }
        return pts.reduce(.zero, +) / Float(pts.count)
    }
}

// MARK: - 30 Hz ticker

private final class LabelTicker: ObservableObject {

    @Published private(set) var tick: Int = 0
    private var timer: Timer?

    init() {
        timer = Timer.scheduledTimer(withTimeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
            self?.tick &+= 1
        }
    }

    deinit { timer?.invalidate() }
}

// MARK: - Confirmation item (Identifiable wrapper for sheet binding)

private struct ConfirmationItem: Identifiable {
    let id: String
    let confirmation: PendingConfirmation

    init(confirmation: PendingConfirmation) {
        self.id = confirmation.callId
        self.confirmation = confirmation
    }
}
