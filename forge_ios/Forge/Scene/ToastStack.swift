import SwiftUI

// Toast notification stack. Toasts are driven by SessionViewModel.hudStatus.stubModes
// (stub-mode transitions) and connection state changes.
// The stack auto-dismisses each toast after a configurable duration.

// MARK: - Toast model

struct Toast: Identifiable, Equatable {
    let id = UUID()
    let message: String
    let symbol: String
    let accent: Color
}

// MARK: - Toast manager (ObservableObject consumed by ToastStack)

@MainActor
final class ToastManager: ObservableObject {

    @Published private(set) var toasts: [Toast] = []

    func show(_ toast: Toast, duration: TimeInterval = 3.0) {
        toasts.append(toast)
        let toastId = toast.id
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: UInt64(duration * 1_000_000_000))
            toasts.removeAll { $0.id == toastId }
        }
    }

    func dismiss(_ toast: Toast) {
        toasts.removeAll { $0.id == toast.id }
    }
}

// MARK: - ToastStack view

struct ToastStack: View {

    @Environment(SessionViewModel.self) private var vm
    @StateObject private var manager = ToastManager()

    var body: some View {
        VStack(spacing: 8) {
            ForEach(manager.toasts) { toast in
                ToastRow(toast: toast) {
                    manager.dismiss(toast)
                }
                .transition(.asymmetric(
                    insertion: .move(edge: .top).combined(with: .opacity),
                    removal: .opacity
                ))
            }
        }
        .animation(.easeInOut(duration: 0.25), value: manager.toasts.map { $0.id })
        .onChange(of: vm.connection) { old, new in
            handleConnectionChange(from: old, to: new)
        }
    }

    // MARK: - Event → toast

    private func handleConnectionChange(from old: ConnectionState, to new: ConnectionState) {
        switch new {
        case .open:
            if old != .open {
                manager.show(Toast(
                    message: "Connected to orchestrator",
                    symbol: "wifi",
                    accent: PanelTheme.riskColor(.low)
                ))
            }
        case .degraded(let reason):
            manager.show(Toast(
                message: reason,
                symbol: "wifi.exclamationmark",
                accent: PanelTheme.riskColor(.medium)
            ), duration: 5.0)
        case .closed:
            if old != .closed && old != .connecting {
                manager.show(Toast(
                    message: "Session closed",
                    symbol: "xmark.circle",
                    accent: PanelTheme.riskColor(.high)
                ))
            }
        case .connecting:
            break
        }
    }
}

// MARK: - Single toast row

private struct ToastRow: View {

    let toast: Toast
    let onDismiss: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: toast.symbol)
                .foregroundStyle(toast.accent)
            Text(toast.message)
                .font(PanelTheme.captionFont)
                .foregroundStyle(PanelTheme.primaryText)
                .lineLimit(2)
            Spacer()
            Button(action: onDismiss) {
                Image(systemName: "xmark")
                    .font(.caption2)
                    .foregroundStyle(PanelTheme.secondaryText)
            }
        }
        .padding(.horizontal, PanelTheme.panelPadding)
        .padding(.vertical, 8)
        .background(PanelTheme.toastBackground)
        .clipShape(RoundedRectangle(cornerRadius: PanelTheme.pillCornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: PanelTheme.pillCornerRadius)
                .stroke(toast.accent.opacity(0.5), lineWidth: 1)
        )
        .padding(.horizontal, 16)
    }
}
