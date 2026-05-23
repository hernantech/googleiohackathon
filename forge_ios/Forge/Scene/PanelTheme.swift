import SwiftUI
import UIKit

// Single source of truth for colors, typography, and depth/opacity used across
// all Scene/ panels. Token names mirror forge_quest/UX_DESIGN.md where applicable.

enum PanelTheme {

    // MARK: - Background fills

    static let panelBackground = Color(white: 0.08, opacity: 0.85)
    static let panelBackgroundLight = Color(white: 0.14, opacity: 0.9)
    static let sheetBackground = Color(white: 0.10, opacity: 0.95)
    static let degradedBackground = Color(red: 0.6, green: 0.15, blue: 0.0, opacity: 0.92)
    static let toastBackground = Color(white: 0.12, opacity: 0.9)

    // MARK: - Accent / category colors

    /// IC / microcontroller
    static let accentIC = Color(red: 1.0, green: 0.55, blue: 0.0)
    /// Passive (resistor, capacitor, inductor)
    static let accentPassive = Color(red: 0.0, green: 0.85, blue: 0.9)
    /// Connector
    static let accentConnector = Color(red: 0.9, green: 0.0, blue: 0.9)
    /// Default / unknown
    static let accentDefault = Color.white

    /// Highlighted version (boosted brightness). `.brightness` is a View
    /// modifier, so boost via HSB on the underlying UIColor instead.
    static func highlighted(_ base: Color) -> Color {
        var h: CGFloat = 0, s: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        guard UIColor(base).getHue(&h, saturation: &s, brightness: &b, alpha: &a) else { return base }
        return Color(hue: h, saturation: s, brightness: min(1, b + 0.35), opacity: a)
    }

    // MARK: - Risk colors

    static func riskColor(_ risk: Risk) -> Color {
        switch risk {
        case .low:    return Color(red: 0.0, green: 0.78, blue: 0.35)
        case .medium: return Color(red: 1.0, green: 0.72, blue: 0.0)
        case .high:   return Color(red: 0.95, green: 0.2, blue: 0.1)
        }
    }

    // MARK: - Text colors

    static let primaryText = Color.white
    static let secondaryText = Color(white: 0.65)
    static let captionText = Color(white: 0.45)

    // MARK: - Typography

    static let labelFont      = Font.system(size: 11, weight: .semibold, design: .monospaced)
    static let bodyFont       = Font.system(size: 13, weight: .regular)
    static let captionFont    = Font.system(size: 11, weight: .regular)
    static let headlineFont   = Font.system(size: 15, weight: .semibold)
    static let hudFont        = Font.system(size: 11, weight: .medium, design: .monospaced)

    // MARK: - Layout constants

    static let cornerRadius: CGFloat = 10
    static let pillCornerRadius: CGFloat = 8
    static let panelWidth: CGFloat = 300
    static let cardMaxWidth: CGFloat = 340
    static let hudPadding: CGFloat = 8
    static let panelPadding: CGFloat = 12

    // MARK: - Author kind colors

    static func authorColor(_ kind: AuthorKind) -> Color {
        switch kind {
        case .user:   return accentPassive
        case .live:   return Color(red: 0.4, green: 0.85, blue: 1.0)
        case .sme:    return accentIC
        case .system: return secondaryText
        }
    }

    /// Confidence 0…1 → green → yellow → red gradient.
    static func confidenceColor(_ value: Float) -> Color {
        let v = Double(max(0, min(1, value)))
        if v >= 0.75 { return Color(red: 0.0, green: 0.78, blue: 0.35) }
        if v >= 0.4  { return Color(red: 1.0, green: 0.72, blue: 0.0) }
        return Color(red: 0.95, green: 0.2, blue: 0.1)
    }

    // MARK: - Component category inference

    /// Infer outline color from id/partNumber prefix conventions.
    static func outlineColor(for component: DetectedComponent) -> Color {
        let prefix = component.id.prefix(1).uppercased()
        switch prefix {
        case "U": return accentIC
        case "R", "C", "L": return accentPassive
        case "J", "P", "CN": return accentConnector
        default:
            let pn = component.partNumber.lowercased()
            if pn.contains("stm") || pn.contains("nrf") || pn.contains("esp") { return accentIC }
            if pn.contains("res") || pn.contains("cap") || pn.contains("ind") { return accentPassive }
            if pn.contains("conn") || pn.contains("hdr") { return accentConnector }
            return accentDefault
        }
    }
}
