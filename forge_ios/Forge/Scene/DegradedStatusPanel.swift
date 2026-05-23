import SwiftUI

// Top banner shown when the orchestrator connection is degraded or closed.
// Fades in/out automatically based on whether it is present in the view tree.

struct DegradedStatusPanel: View {

    let reason: String

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "wifi.exclamationmark")
                .font(.caption.weight(.semibold))
            Text(reason)
                .font(PanelTheme.captionFont)
                .lineLimit(1)
            Spacer()
        }
        .foregroundStyle(.white)
        .padding(.horizontal, PanelTheme.panelPadding)
        .padding(.vertical, 6)
        .background(PanelTheme.degradedBackground)
        .clipShape(RoundedRectangle(cornerRadius: PanelTheme.pillCornerRadius))
        .transition(.opacity.combined(with: .move(edge: .top)))
    }
}
