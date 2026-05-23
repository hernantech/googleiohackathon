import SwiftUI

// Modal sheet driven by vm.pendingConfirmation.
// Renders ActionCard when present; 3-second countdown disables Affirm for HIGH risk.

struct ConfirmationSheet: View {

    @Environment(SessionViewModel.self) private var vm
    @Environment(\.dismiss) private var dismiss

    let confirmation: PendingConfirmation

    @State private var countdown: Int = 0
    @State private var countdownTask: Task<Void, Never>? = nil

    var body: some View {
        VStack(spacing: 20) {
            riskHeader
            invokerRow
            if let card = confirmation.actionCard {
                actionCardSection(card)
            } else {
                summaryBlock
            }
            Spacer()
            actionButtons
        }
        .padding(PanelTheme.panelPadding * 2)
        .background(PanelTheme.sheetBackground)
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
        .onAppear { startCountdownIfNeeded() }
        .onDisappear { countdownTask?.cancel() }
    }

    // MARK: - Risk header

    private var riskHeader: some View {
        HStack(spacing: 10) {
            Image(systemName: riskSymbol)
                .font(.title2)
                .foregroundStyle(PanelTheme.riskColor(confirmation.risk))
            VStack(alignment: .leading, spacing: 2) {
                Text("Confirmation Required")
                    .font(PanelTheme.headlineFont)
                    .foregroundStyle(PanelTheme.primaryText)
                HStack(spacing: 6) {
                    RiskPill(risk: confirmation.risk)
                    Text(riskLabel)
                        .font(PanelTheme.captionFont)
                        .foregroundStyle(PanelTheme.riskColor(confirmation.risk))
                }
            }
            Spacer()
        }
    }

    // MARK: - Invoker identity

    @ViewBuilder
    private var invokerRow: some View {
        if let sme = confirmation.invokerSmeId {
            HStack(spacing: 8) {
                Image(systemName: "person.badge.shield.checkmark")
                    .foregroundStyle(PanelTheme.authorColor(.sme))
                Text("Requested by")
                    .font(PanelTheme.captionFont)
                    .foregroundStyle(PanelTheme.captionText)
                Text(sme)
                    .font(PanelTheme.labelFont)
                    .foregroundStyle(PanelTheme.authorColor(.sme))
                Spacer()
            }
            .padding(8)
            .background(PanelTheme.panelBackground)
            .clipShape(RoundedRectangle(cornerRadius: PanelTheme.pillCornerRadius))
        }
    }

    // MARK: - ActionCard section

    private func actionCardSection(_ card: ActionCard) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text(card.title)
                    .font(PanelTheme.headlineFont)
                    .foregroundStyle(PanelTheme.primaryText)
                markdownBlock(card.bodyMarkdown)
                if let diff = card.diffMarkdown {
                    DiffTable(diffMarkdown: diff)
                }
            }
            .padding(PanelTheme.panelPadding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(PanelTheme.panelBackground)
            .clipShape(RoundedRectangle(cornerRadius: PanelTheme.cornerRadius))
        }
    }

    // MARK: - Plain summary block (no ActionCard)

    private var summaryBlock: some View {
        Text(confirmation.summary)
            .font(PanelTheme.bodyFont)
            .foregroundStyle(PanelTheme.secondaryText)
            .multilineTextAlignment(.leading)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(PanelTheme.panelPadding)
            .background(PanelTheme.panelBackground)
            .clipShape(RoundedRectangle(cornerRadius: PanelTheme.cornerRadius))
    }

    // MARK: - Action buttons

    private var actionButtons: some View {
        let affirmLabel = confirmation.actionCard?.affirmLabel ?? "Approve"
        let denyLabel   = confirmation.actionCard?.denyLabel   ?? "Deny"
        return HStack(spacing: 16) {
            Button(role: .destructive) {
                vm.send(.confirmationRejected(callId: confirmation.callId))
                dismiss()
            } label: {
                Text(denyLabel)
                    .font(PanelTheme.headlineFont)
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(PanelTheme.riskColor(.high).opacity(0.8))

            Button {
                vm.send(.confirmationAccepted(callId: confirmation.callId))
                dismiss()
            } label: {
                Group {
                    if countdown > 0 {
                        Text("\(affirmLabel) (\(countdown)s)")
                    } else {
                        Text(affirmLabel)
                    }
                }
                .font(PanelTheme.headlineFont)
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(PanelTheme.riskColor(.low))
            .disabled(countdown > 0)
        }
    }

    // MARK: - HIGH risk countdown

    private func startCountdownIfNeeded() {
        guard confirmation.risk == .high else { return }
        countdown = 3
        countdownTask = Task { @MainActor in
            while countdown > 0 {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                guard !Task.isCancelled else { return }
                countdown -= 1
            }
        }
    }

    // MARK: - Helpers

    private var riskLabel: String {
        switch confirmation.risk {
        case .low:    return "Low risk"
        case .medium: return "Medium risk"
        case .high:   return "High risk — review carefully"
        }
    }

    private var riskSymbol: String {
        switch confirmation.risk {
        case .low:    return "checkmark.shield"
        case .medium: return "exclamationmark.triangle"
        case .high:   return "exclamationmark.octagon.fill"
        }
    }

    private func markdownBlock(_ raw: String) -> some View {
        let attr = (try? AttributedString(markdown: raw)) ?? AttributedString(raw)
        return Text(attr)
            .font(PanelTheme.bodyFont)
            .foregroundStyle(PanelTheme.secondaryText)
            .fixedSize(horizontal: false, vertical: true)
    }
}

// MARK: - Diff table (Current / Proposed columns)

private struct DiffTable: View {

    let diffMarkdown: String

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 0) {
                columnHeader("Current")
                Divider()
                columnHeader("Proposed")
            }
            Divider()
            HStack(alignment: .top, spacing: 0) {
                diffColumn(lines: beforeLines, highlight: Color(red: 0.95, green: 0.2, blue: 0.1).opacity(0.12))
                Divider()
                diffColumn(lines: afterLines, highlight: Color(red: 0.0, green: 0.78, blue: 0.35).opacity(0.12))
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: PanelTheme.pillCornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: PanelTheme.pillCornerRadius)
                .stroke(PanelTheme.secondaryText.opacity(0.25), lineWidth: 1)
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

    private func columnHeader(_ title: String) -> some View {
        Text(title)
            .font(PanelTheme.captionFont)
            .foregroundStyle(PanelTheme.captionText)
            .padding(6)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(PanelTheme.panelBackground)
    }

    private func diffColumn(lines: [String], highlight: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
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
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(highlight)
    }
}
