import SwiftUI

// Compact pill label for a detected component.
// Expands when isFocused to show secondary info and confidence.

struct ComponentLabel: View {

    let component: DetectedComponent
    let isFocused: Bool
    let onTap: () -> Void

    private var accentColor: Color { PanelTheme.outlineColor(for: component) }

    var body: some View {
        Button(action: onTap) {
            if isFocused {
                expandedPill
            } else {
                compactPill
            }
        }
        .buttonStyle(.plain)
        .animation(.easeInOut(duration: 0.15), value: isFocused)
    }

    // MARK: - Pill variants

    private var compactPill: some View {
        HStack(spacing: 4) {
            Circle()
                .fill(accentColor)
                .frame(width: 6, height: 6)
            Text(component.id)
                .font(PanelTheme.labelFont)
                .foregroundStyle(PanelTheme.primaryText)
        }
        .padding(.horizontal, 7)
        .padding(.vertical, 4)
        .background(PanelTheme.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: PanelTheme.pillCornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: PanelTheme.pillCornerRadius)
                .stroke(accentColor.opacity(0.7), lineWidth: 1)
        )
    }

    private var expandedPill: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 4) {
                Circle()
                    .fill(accentColor)
                    .frame(width: 6, height: 6)
                Text(component.id)
                    .font(PanelTheme.labelFont)
                    .foregroundStyle(PanelTheme.primaryText)
            }
            Text(component.partNumber)
                .font(PanelTheme.captionFont)
                .foregroundStyle(PanelTheme.secondaryText)
            if let secondary = component.secondary {
                Text(secondary)
                    .font(PanelTheme.captionFont)
                    .foregroundStyle(PanelTheme.captionText)
            }
            Text(String(format: "%.0f%%", component.confidence * 100))
                .font(PanelTheme.captionFont)
                .foregroundStyle(accentColor.opacity(0.85))
        }
        .padding(PanelTheme.hudPadding)
        .background(PanelTheme.panelBackgroundLight)
        .clipShape(RoundedRectangle(cornerRadius: PanelTheme.pillCornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: PanelTheme.pillCornerRadius)
                .stroke(accentColor, lineWidth: 1.5)
        )
    }
}
