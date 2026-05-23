import SwiftUI

// Renders a typed ChatCard parsed from a JSON ChatMessage body.
// Each case maps to a distinct visual treatment.

struct ChatCardView: View {

    let card: ChatCard?

    var body: some View {
        Group {
            switch card {
            case .smeResponse(let r):     SmeResponseCard(response: r)
            case .dissentReport(let r):   DissentReportCard(report: r)
            case .actionCard(let a):      ActionCardView(card: a)
            case .mergedOpinion(let m):   MergedOpinionCard(opinion: m)
            case .safetyInterrupt(let s): SafetyInterruptCard(interrupt: s)
            case .snapshotAnalysis(let sa): SnapshotAnalysisCard(analysis: sa)
            case .toolResult(let name, let json): RawDisclosure(label: name, json: json)
            case .unsupported(let kind, let json): RawDisclosure(label: "raw:\(kind)", json: json)
            case nil:
                Text("(unreadable card)")
                    .font(PanelTheme.captionFont)
                    .foregroundStyle(PanelTheme.captionText)
            }
        }
    }
}

// MARK: - SME Response

private struct SmeResponseCard: View {

    let response: SmeResponse
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                confidenceChip
                Text(response.smeId)
                    .font(PanelTheme.labelFont)
                    .foregroundStyle(PanelTheme.authorColor(.sme))
                    .lineLimit(1)
            }
            Text(response.claim)
                .font(PanelTheme.headlineFont)
                .foregroundStyle(PanelTheme.primaryText)
                .fixedSize(horizontal: false, vertical: true)
            if expanded {
                Text(response.rationale)
                    .font(PanelTheme.bodyFont)
                    .foregroundStyle(PanelTheme.secondaryText)
                    .fixedSize(horizontal: false, vertical: true)
                if !response.evidence.isEmpty {
                    evidenceRow
                }
                if !response.proposedActions.isEmpty {
                    actionsRow
                }
            }
            Button {
                withAnimation(.easeInOut(duration: 0.2)) { expanded.toggle() }
            } label: {
                Label(expanded ? "Less" : "More", systemImage: expanded ? "chevron.up" : "chevron.down")
                    .font(PanelTheme.captionFont)
                    .foregroundStyle(PanelTheme.captionText)
            }
            .buttonStyle(.plain)
        }
        .padding(PanelTheme.panelPadding)
        .background(PanelTheme.panelBackgroundLight)
        .clipShape(RoundedRectangle(cornerRadius: PanelTheme.cornerRadius))
    }

    private var confidenceChip: some View {
        let pct = Int(response.confidence * 100)
        return Text("\(pct)%")
            .font(PanelTheme.labelFont)
            .foregroundStyle(.black)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(PanelTheme.confidenceColor(response.confidence))
            .clipShape(Capsule())
    }

    private var evidenceRow: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(response.evidence, id: \.uri) { ev in
                    EvidenceChip(evidence: ev)
                }
            }
        }
    }

    private var actionsRow: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(response.proposedActions, id: \.tool) { action in
                    ActionChip(action: action)
                }
            }
        }
    }
}

// MARK: - Dissent Report

private struct DissentReportCard: View {

    let report: DissentReport

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            dissentHeader
            Divider().background(Color(red: 1.0, green: 0.3, blue: 0.2).opacity(0.6))
            if !report.pairwise.isEmpty {
                ForEach(report.pairwise.indices, id: \.self) { i in
                    DissentPairRow(pair: report.pairwise[i])
                    if i < report.pairwise.count - 1 {
                        Divider().background(PanelTheme.secondaryText.opacity(0.2))
                    }
                }
            }
            summaryBanner
        }
        .padding(PanelTheme.panelPadding)
        .background(Color(red: 0.18, green: 0.05, blue: 0.05, opacity: 0.92))
        .clipShape(RoundedRectangle(cornerRadius: PanelTheme.cornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: PanelTheme.cornerRadius)
                .stroke(Color(red: 1.0, green: 0.3, blue: 0.2).opacity(0.6), lineWidth: 1)
        )
    }

    private var dissentHeader: some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(Color(red: 1.0, green: 0.3, blue: 0.2))
            Text("Dissent — \(report.axis)")
                .font(PanelTheme.headlineFont)
                .foregroundStyle(PanelTheme.primaryText)
            Spacer()
            Text(report.parties.joined(separator: " vs "))
                .font(PanelTheme.captionFont)
                .foregroundStyle(PanelTheme.captionText)
                .lineLimit(1)
        }
    }

    private var summaryBanner: some View {
        Text(report.summary)
            .font(PanelTheme.bodyFont)
            .foregroundStyle(PanelTheme.secondaryText)
            .fixedSize(horizontal: false, vertical: true)
            .padding(8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(PanelTheme.panelBackground)
            .clipShape(RoundedRectangle(cornerRadius: PanelTheme.pillCornerRadius))
    }
}

private struct DissentPairRow: View {

    let pair: DissentPair

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 6) {
                claimColumn(label: pair.a, claim: pair.aClaim, side: .leading)
                Divider()
                    .frame(height: 60)
                claimColumn(label: pair.b, claim: pair.bClaim, side: .trailing)
            }
            HStack(spacing: 4) {
                Image(systemName: "arrow.triangle.branch")
                    .font(.caption2)
                    .foregroundStyle(PanelTheme.captionText)
                Text("Crux: \(pair.crux)")
                    .font(PanelTheme.captionFont)
                    .foregroundStyle(PanelTheme.captionText)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func claimColumn(label: String, claim: String, side: HorizontalAlignment) -> some View {
        VStack(alignment: side, spacing: 4) {
            Text(label)
                .font(PanelTheme.labelFont)
                .foregroundStyle(PanelTheme.authorColor(.sme))
            Text(claim)
                .font(PanelTheme.captionFont)
                .foregroundStyle(PanelTheme.primaryText)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: Alignment(horizontal: side, vertical: .top))
    }
}

// MARK: - Action Card

private struct ActionCardView: View {

    let card: ActionCard

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(card.title)
                    .font(PanelTheme.headlineFont)
                    .foregroundStyle(PanelTheme.primaryText)
                Spacer()
                RiskPill(risk: card.risk)
            }
            markdownText(card.bodyMarkdown)
            if let diff = card.diffMarkdown {
                DiffBlock(diffMarkdown: diff)
            }
        }
        .padding(PanelTheme.panelPadding)
        .background(PanelTheme.panelBackgroundLight)
        .clipShape(RoundedRectangle(cornerRadius: PanelTheme.cornerRadius))
    }
}

// MARK: - Merged Opinion

private struct MergedOpinionCard: View {

    let opinion: MergedOpinion

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Image(systemName: "checkmark.seal.fill")
                    .foregroundStyle(PanelTheme.riskColor(.low))
                Text(opinion.headline)
                    .font(PanelTheme.headlineFont)
                    .foregroundStyle(PanelTheme.primaryText)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !opinion.supportingSmes.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(opinion.supportingSmes, id: \.self) { sme in
                            Text(sme)
                                .font(PanelTheme.labelFont)
                                .foregroundStyle(PanelTheme.authorColor(.sme))
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(PanelTheme.panelBackground)
                                .clipShape(Capsule())
                        }
                    }
                }
            }
            if !opinion.openQuestions.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Open questions")
                        .font(PanelTheme.captionFont)
                        .foregroundStyle(PanelTheme.captionText)
                    ForEach(opinion.openQuestions, id: \.self) { q in
                        HStack(alignment: .top, spacing: 6) {
                            Text("?")
                                .font(PanelTheme.captionFont)
                                .foregroundStyle(PanelTheme.captionText)
                            Text(q)
                                .font(PanelTheme.captionFont)
                                .foregroundStyle(PanelTheme.secondaryText)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
        }
        .padding(PanelTheme.panelPadding)
        .background(PanelTheme.panelBackgroundLight)
        .clipShape(RoundedRectangle(cornerRadius: PanelTheme.cornerRadius))
    }
}

// MARK: - Safety Interrupt Card (inline in chat)

private struct SafetyInterruptCard: View {

    let interrupt: SafetyInterrupt

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: interrupt.severity == .halt ? "xmark.octagon.fill" : "exclamationmark.triangle.fill")
                .foregroundStyle(severityColor)
            Text(interrupt.reason)
                .font(PanelTheme.bodyFont)
                .foregroundStyle(PanelTheme.primaryText)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(PanelTheme.panelPadding)
        .background(severityColor.opacity(0.15))
        .clipShape(RoundedRectangle(cornerRadius: PanelTheme.cornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: PanelTheme.cornerRadius)
                .stroke(severityColor.opacity(0.6), lineWidth: 1)
        )
    }

    private var severityColor: Color {
        interrupt.severity == .halt
            ? Color(red: 0.95, green: 0.2, blue: 0.1)
            : Color(red: 1.0, green: 0.72, blue: 0.0)
    }
}

// MARK: - Snapshot Analysis Card (specs/00 §4.2, specs/04 §3.1 CB-11)

private struct SnapshotAnalysisCard: View {

    let analysis: SnapshotAnalysis
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Image(systemName: "camera.viewfinder")
                    .foregroundStyle(PanelTheme.accentIC)
                Text("Snapshot — \(analysis.model)")
                    .font(PanelTheme.labelFont)
                    .foregroundStyle(PanelTheme.primaryText)
                Spacer()
                Text("\(analysis.frame.width)×\(analysis.frame.height)")
                    .font(PanelTheme.captionFont)
                    .foregroundStyle(PanelTheme.captionText)
            }
            Text(analysis.analysis)
                .font(PanelTheme.bodyFont)
                .foregroundStyle(PanelTheme.secondaryText)
                .lineLimit(expanded ? nil : 4)
                .fixedSize(horizontal: false, vertical: expanded)
            if !analysis.cites.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(analysis.cites, id: \.uri) { cite in
                            EvidenceChip(evidence: cite)
                        }
                    }
                }
            }
            if analysis.analysis.count > 200 {
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) { expanded.toggle() }
                } label: {
                    Label(expanded ? "Less" : "More", systemImage: expanded ? "chevron.up" : "chevron.down")
                        .font(PanelTheme.captionFont)
                        .foregroundStyle(PanelTheme.captionText)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(PanelTheme.panelPadding)
        .background(PanelTheme.panelBackgroundLight)
        .clipShape(RoundedRectangle(cornerRadius: PanelTheme.cornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: PanelTheme.cornerRadius)
                .stroke(PanelTheme.accentIC.opacity(0.4), lineWidth: 1)
        )
    }
}

// MARK: - Raw JSON disclosure

private struct RawDisclosure: View {

    let label: String
    let json: String
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Button {
                withAnimation(.easeInOut(duration: 0.15)) { expanded.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: expanded ? "chevron.down" : "chevron.right")
                        .font(.caption2)
                    Text(label)
                        .font(PanelTheme.labelFont)
                }
                .foregroundStyle(PanelTheme.captionText)
            }
            .buttonStyle(.plain)
            if expanded {
                Text(json)
                    .font(.system(size: 10, weight: .regular, design: .monospaced))
                    .foregroundStyle(PanelTheme.secondaryText)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(6)
                    .background(PanelTheme.panelBackground)
                    .clipShape(RoundedRectangle(cornerRadius: PanelTheme.pillCornerRadius))
            }
        }
        .padding(.horizontal, PanelTheme.panelPadding)
        .padding(.vertical, 4)
    }
}

// MARK: - Shared sub-components

struct RiskPill: View {
    let risk: Risk
    var body: some View {
        Text(risk.rawValue)
            .font(PanelTheme.labelFont)
            .foregroundStyle(.black)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(PanelTheme.riskColor(risk))
            .clipShape(Capsule())
    }
}

private struct EvidenceChip: View {
    let evidence: EvidenceRef
    var body: some View {
        Label(evidence.kind, systemImage: evidenceSymbol)
            .font(PanelTheme.captionFont)
            .foregroundStyle(PanelTheme.secondaryText)
            .padding(.horizontal, 6)
            .padding(.vertical, 3)
            .background(PanelTheme.panelBackground)
            .clipShape(Capsule())
    }
    private var evidenceSymbol: String {
        switch evidence.kind {
        case "frame":         return "camera"
        case "scope_capture": return "waveform"
        case "datasheet":     return "doc.text"
        case "url":           return "link"
        default:              return "paperclip"
        }
    }
}

private struct ActionChip: View {
    let action: ProposedAction
    var body: some View {
        HStack(spacing: 4) {
            Circle()
                .fill(PanelTheme.riskColor(action.risk))
                .frame(width: 6, height: 6)
            Text(action.tool)
                .font(PanelTheme.captionFont)
                .foregroundStyle(PanelTheme.primaryText)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(PanelTheme.panelBackgroundLight)
        .clipShape(Capsule())
    }
}

private struct DiffBlock: View {

    let diffMarkdown: String

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                diffColumn(title: "Current", lines: beforeLines, color: Color(red: 0.95, green: 0.2, blue: 0.1).opacity(0.15))
                Divider()
                diffColumn(title: "Proposed", lines: afterLines, color: Color(red: 0.0, green: 0.78, blue: 0.35).opacity(0.15))
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: PanelTheme.pillCornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: PanelTheme.pillCornerRadius)
                .stroke(PanelTheme.secondaryText.opacity(0.2), lineWidth: 1)
        )
    }

    private var beforeLines: [String] {
        diffMarkdown.split(separator: "\n", omittingEmptySubsequences: false)
            .filter { $0.hasPrefix("-") }
            .map { String($0.dropFirst()) }
    }

    private var afterLines: [String] {
        diffMarkdown.split(separator: "\n", omittingEmptySubsequences: false)
            .filter { $0.hasPrefix("+") }
            .map { String($0.dropFirst()) }
    }

    private func diffColumn(title: String, lines: [String], color: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(PanelTheme.captionFont)
                .foregroundStyle(PanelTheme.captionText)
                .padding(.bottom, 2)
            if lines.isEmpty {
                Text("—")
                    .font(.system(size: 11, weight: .regular, design: .monospaced))
                    .foregroundStyle(PanelTheme.captionText)
            } else {
                ForEach(lines, id: \.self) { line in
                    Text(line)
                        .font(.system(size: 11, weight: .regular, design: .monospaced))
                        .foregroundStyle(PanelTheme.primaryText)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(6)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(color)
    }
}

private func markdownText(_ raw: String) -> some View {
    let attributed = (try? AttributedString(markdown: raw)) ?? AttributedString(raw)
    return Text(attributed)
        .font(PanelTheme.bodyFont)
        .foregroundStyle(PanelTheme.secondaryText)
        .fixedSize(horizontal: false, vertical: true)
}
