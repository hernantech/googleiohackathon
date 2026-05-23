import SwiftUI

// Tap-to-expand card that surfaces component metadata.
// Shown when a component is focused (detections.focusedId == component.id).

struct ComponentDetailCard: View {

    let component: DetectedComponent
    let onDismiss: () -> Void

    private var accentColor: Color { PanelTheme.outlineColor(for: component) }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            Divider().background(accentColor.opacity(0.4))
            detailRows
            Spacer(minLength: 0)
        }
        .padding(PanelTheme.panelPadding)
        .frame(maxWidth: PanelTheme.cardMaxWidth)
        .background(PanelTheme.sheetBackground)
        .clipShape(RoundedRectangle(cornerRadius: PanelTheme.cornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: PanelTheme.cornerRadius)
                .stroke(accentColor.opacity(0.5), lineWidth: 1)
        )
    }

    // MARK: - Subviews

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(component.id)
                    .font(PanelTheme.headlineFont)
                    .foregroundStyle(accentColor)
                Text(component.partNumber)
                    .font(PanelTheme.bodyFont)
                    .foregroundStyle(PanelTheme.primaryText)
            }
            Spacer()
            Button(action: onDismiss) {
                Image(systemName: "xmark.circle.fill")
                    .foregroundStyle(PanelTheme.secondaryText)
            }
        }
    }

    private var detailRows: some View {
        VStack(alignment: .leading, spacing: 6) {
            row(label: "Confidence", value: String(format: "%.1f%%", component.confidence * 100))
            if let secondary = component.secondary {
                row(label: "Note", value: secondary)
            }
            row(label: "BBox",
                value: "(\(component.bbox.x1),\(component.bbox.y1))–(\(component.bbox.x2),\(component.bbox.y2))")
        }
    }

    private func row(label: String, value: String) -> some View {
        HStack(alignment: .top) {
            Text(label)
                .font(PanelTheme.captionFont)
                .foregroundStyle(PanelTheme.captionText)
                .frame(width: 80, alignment: .leading)
            Text(value)
                .font(PanelTheme.captionFont)
                .foregroundStyle(PanelTheme.secondaryText)
                .multilineTextAlignment(.leading)
        }
    }
}
