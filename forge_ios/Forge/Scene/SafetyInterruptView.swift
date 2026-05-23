import SwiftUI

// Driven by vm.safetyInterrupt.
// WARN → sticky dismissable yellow banner.
// HALT → full-screen red takeover that blocks all interaction until acknowledged.

struct SafetyInterruptView: View {

    @Environment(SessionViewModel.self) private var vm

    var body: some View {
        if let interrupt = vm.safetyInterrupt {
            switch interrupt.severity {
            case .warn:
                WarnBanner(interrupt: interrupt, onDismiss: { vm.dismissSafetyInterrupt() })
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .zIndex(100)
            case .halt:
                HaltTakeover(interrupt: interrupt, onAcknowledge: { vm.dismissSafetyInterrupt() })
                    .transition(.opacity)
                    .zIndex(200)
            }
        }
    }
}

// MARK: - WARN banner

private struct WarnBanner: View {

    let interrupt: SafetyInterrupt
    let onDismiss: () -> Void

    private let warnYellow = Color(red: 1.0, green: 0.72, blue: 0.0)

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(warnYellow)
                Text(interrupt.reason)
                    .font(PanelTheme.bodyFont)
                    .foregroundStyle(PanelTheme.primaryText)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer()
                Button(action: onDismiss) {
                    Image(systemName: "xmark")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(PanelTheme.secondaryText)
                }
                .buttonStyle(.plain)
            }
            if !interrupt.suggestedRecoverActions.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(interrupt.suggestedRecoverActions, id: \.tool) { action in
                            recoverChip(action)
                        }
                    }
                }
            }
        }
        .padding(.horizontal, PanelTheme.panelPadding)
        .padding(.vertical, 10)
        .background(warnYellow.opacity(0.18))
        .overlay(
            Rectangle()
                .frame(height: 2)
                .foregroundStyle(warnYellow)
                .frame(maxHeight: .infinity, alignment: .top)
        )
    }

    private func recoverChip(_ action: ProposedAction) -> some View {
        Text(action.tool)
            .font(PanelTheme.captionFont)
            .foregroundStyle(.black)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(warnYellow)
            .clipShape(Capsule())
    }
}

// MARK: - HALT full-screen takeover

private struct HaltTakeover: View {

    let interrupt: SafetyInterrupt
    let onAcknowledge: () -> Void

    private let haltRed = Color(red: 0.95, green: 0.2, blue: 0.1)

    var body: some View {
        ZStack {
            haltRed.opacity(0.92).ignoresSafeArea()
            VStack(spacing: 28) {
                Image(systemName: "xmark.octagon.fill")
                    .font(.system(size: 64))
                    .foregroundStyle(.white)
                Text("SAFETY HALT")
                    .font(.system(size: 28, weight: .black))
                    .foregroundStyle(.white)
                Text(interrupt.reason)
                    .font(PanelTheme.headlineFont)
                    .foregroundStyle(.white.opacity(0.9))
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
                    .fixedSize(horizontal: false, vertical: true)
                if !interrupt.suggestedRecoverActions.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Suggested recovery")
                            .font(PanelTheme.captionFont)
                            .foregroundStyle(.white.opacity(0.7))
                        ForEach(interrupt.suggestedRecoverActions, id: \.tool) { action in
                            HStack(spacing: 8) {
                                Circle()
                                    .fill(PanelTheme.riskColor(action.risk))
                                    .frame(width: 8, height: 8)
                                Text(action.tool)
                                    .font(PanelTheme.bodyFont)
                                    .foregroundStyle(.white)
                            }
                        }
                    }
                    .padding(PanelTheme.panelPadding)
                    .background(.white.opacity(0.1))
                    .clipShape(RoundedRectangle(cornerRadius: PanelTheme.cornerRadius))
                }
                Button(action: onAcknowledge) {
                    Text("Acknowledge")
                        .font(PanelTheme.headlineFont)
                        .foregroundStyle(haltRed)
                        .padding(.horizontal, 32)
                        .padding(.vertical, 14)
                        .background(.white)
                        .clipShape(RoundedRectangle(cornerRadius: PanelTheme.cornerRadius))
                }
                .buttonStyle(.plain)
            }
            .padding(32)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .ignoresSafeArea()
    }
}
