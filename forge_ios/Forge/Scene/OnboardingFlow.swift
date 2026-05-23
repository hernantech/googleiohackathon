import SwiftUI

// First-run device tour. Shown once (keyed by UserDefaults "onboardingComplete").
// Parent dismisses by setting showOnboarding = false in their @State.

struct OnboardingFlow: View {

    @Binding var isPresented: Bool
    @State private var page = 0

    private let pages: [OnboardingPage] = [
        OnboardingPage(
            symbol: "camera.viewfinder",
            title: "Point at your bench",
            body: "Forge uses the rear camera and LiDAR sensor to identify components on your PCB in real time."
        ),
        OnboardingPage(
            symbol: "cpu",
            title: "Components light up",
            body: "Detected ICs, passives, and connectors get color-coded outlines locked to the physical board."
        ),
        OnboardingPage(
            symbol: "bubble.left.and.bubble.right.fill",
            title: "Ask your expert",
            body: "Tap a component or speak a command. A Gemini-powered expert agent answers from datasheets and schematics."
        ),
        OnboardingPage(
            symbol: "gear",
            title: "Settings",
            body: "Set the orchestrator URL and auth token in Settings before connecting to your Forge session."
        )
    ]

    var body: some View {
        ZStack {
            PanelTheme.sheetBackground.ignoresSafeArea()

            VStack(spacing: 28) {
                Spacer()
                pageContent
                Spacer()
                pageIndicator
                navigationButtons
                    .padding(.bottom, 32)
            }
            .padding(.horizontal, 32)
        }
    }

    // MARK: - Current page

    private var pageContent: some View {
        let p = pages[page]
        return VStack(spacing: 20) {
            Image(systemName: p.symbol)
                .font(.system(size: 64, weight: .light))
                .foregroundStyle(PanelTheme.accentIC)
            Text(p.title)
                .font(PanelTheme.headlineFont)
                .foregroundStyle(PanelTheme.primaryText)
                .multilineTextAlignment(.center)
            Text(p.body)
                .font(PanelTheme.bodyFont)
                .foregroundStyle(PanelTheme.secondaryText)
                .multilineTextAlignment(.center)
        }
        .transition(.asymmetric(
            insertion: .move(edge: .trailing),
            removal: .move(edge: .leading)
        ))
        .id(page)
    }

    // MARK: - Page dots

    private var pageIndicator: some View {
        HStack(spacing: 8) {
            ForEach(0..<pages.count, id: \.self) { i in
                Circle()
                    .fill(i == page ? PanelTheme.accentIC : PanelTheme.secondaryText)
                    .frame(width: 7, height: 7)
            }
        }
    }

    // MARK: - Buttons

    private var navigationButtons: some View {
        HStack {
            if page > 0 {
                Button("Back") {
                    withAnimation { page -= 1 }
                }
                .foregroundStyle(PanelTheme.secondaryText)
            }
            Spacer()
            if page < pages.count - 1 {
                Button("Next") {
                    withAnimation { page += 1 }
                }
                .buttonStyle(.borderedProminent)
                .tint(PanelTheme.accentIC)
            } else {
                Button("Get Started") {
                    UserDefaults(suiteName: "ai.forge.settings")?.set(true, forKey: "onboardingComplete")
                    isPresented = false
                }
                .buttonStyle(.borderedProminent)
                .tint(PanelTheme.accentIC)
            }
        }
    }
}

// MARK: - Page model

private struct OnboardingPage {
    let symbol: String
    let title: String
    let body: String
}
