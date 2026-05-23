import SwiftUI

// Top-level content once permissions are granted. Owns the SessionViewModel,
// drives its lifecycle against the scene phase, and gates first-run onboarding.

struct RootView: View {
    @State private var vm = SessionViewModel(config: .load())
    @State private var sessionRunning = false
    @State private var showOnboarding = !(UserDefaults(suiteName: "ai.forge.ios")?.bool(forKey: "onboardingComplete") ?? false)
    @Environment(\.scenePhase) private var scenePhase

    #if DEBUG
    @State private var showSpike = false
    #endif

    var body: some View {
        ForgeRealityView()
            .environment(vm)
            .task {
                if !sessionRunning { sessionRunning = true; await vm.start() }
            }
            .fullScreenCover(isPresented: $showOnboarding) {
                OnboardingFlow(isPresented: $showOnboarding)
            }
            .onChange(of: scenePhase) { _, phase in
                switch phase {
                case .background:
                    if sessionRunning { sessionRunning = false; Task { await vm.stop() } }
                case .active:
                    if !sessionRunning { sessionRunning = true; Task { await vm.start() } }
                default:
                    break
                }
            }
            .overlay(alignment: .bottomLeading) { debugSpikeButton }
        #if DEBUG
            .fullScreenCover(isPresented: $showSpike) { SpikeView() }
        #endif
    }

    // The README's production entry is a long-press app-icon quick action; in
    // DEBUG we expose the same canary via an in-app hidden control.
    @ViewBuilder
    private var debugSpikeButton: some View {
        #if DEBUG
        Button("🔬 Spike") { showSpike = true }
            .font(.caption2)
            .padding(6)
            .background(.ultraThinMaterial, in: Capsule())
            .padding(.leading, 12)
            .padding(.bottom, 28)
        #else
        EmptyView()
        #endif
    }
}
