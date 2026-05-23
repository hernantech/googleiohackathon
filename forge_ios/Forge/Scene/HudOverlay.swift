import SwiftUI

// Top-left status HUD: FPS, session id, and stub-mode badges.

struct HudOverlay: View {

    let status: HudStatus

    var body: some View {
        HStack(spacing: 6) {
            fpsTag
            sessionTag
            ForEach(status.stubModes, id: \.self) { mode in
                stubBadge(mode)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(PanelTheme.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: PanelTheme.pillCornerRadius))
    }

    // MARK: - Subviews

    private var fpsTag: some View {
        Text("\(status.fps) fps")
            .font(PanelTheme.hudFont)
            .foregroundStyle(fpsColor)
    }

    private var fpsColor: Color {
        if status.fps >= 25 { return Color(red: 0.0, green: 0.85, blue: 0.4) }
        if status.fps >= 15 { return Color(red: 1.0, green: 0.72, blue: 0.0) }
        return Color(red: 0.95, green: 0.2, blue: 0.1)
    }

    private var sessionTag: some View {
        Text(truncatedSession)
            .font(PanelTheme.hudFont)
            .foregroundStyle(PanelTheme.secondaryText)
    }

    private var truncatedSession: String {
        let s = status.sessionId
        guard s.count > 8 else { return s }
        return String(s.prefix(4)) + "…" + String(s.suffix(4))
    }

    private func stubBadge(_ mode: String) -> some View {
        Text("STUB:\(mode)")
            .font(PanelTheme.hudFont)
            .foregroundStyle(.black)
            .padding(.horizontal, 4)
            .padding(.vertical, 2)
            .background(Color(red: 1.0, green: 0.72, blue: 0.0))
            .clipShape(RoundedRectangle(cornerRadius: 4))
    }
}
