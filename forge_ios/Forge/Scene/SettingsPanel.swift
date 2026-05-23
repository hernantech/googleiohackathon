import SwiftUI

// Settings panel: edits orchestrator URL and auth token via the
// UserDefaults suite "ai.forge.settings".  Displayed as a sheet.
// (The suite name must NOT equal the bundle id "ai.forge.ios", or
// UserDefaults(suiteName:) returns nil and nothing persists.)

struct SettingsPanel: View {

    @Environment(\.dismiss) private var dismiss

    // Backing defaults suite
    private let defaults = UserDefaults(suiteName: "ai.forge.settings")

    @State private var urlText: String = ""
    @State private var tokenText: String = ""
    @State private var showTokenClear = false

    var body: some View {
        NavigationStack {
            Form {
                orchestratorSection
                authSection
                onboardingSection
            }
            .scrollContentBackground(.hidden)
            .background(PanelTheme.sheetBackground)
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { save(); dismiss() }
                }
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
        .onAppear { loadCurrent() }
    }

    // MARK: - Sections

    private var orchestratorSection: some View {
        Section("Orchestrator") {
            LabeledContent("URL") {
                TextField("ws://host:port/v1/session", text: $urlText)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .keyboardType(.URL)
                    .font(PanelTheme.captionFont)
                    .multilineTextAlignment(.trailing)
            }
        }
    }

    private var authSection: some View {
        Section("Authentication") {
            LabeledContent("Token") {
                SecureField("auth token", text: $tokenText)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .font(PanelTheme.captionFont)
                    .multilineTextAlignment(.trailing)
            }
        }
    }

    private var onboardingSection: some View {
        Section("Onboarding") {
            Button("Reset onboarding tour") {
                defaults?.removeObject(forKey: "onboardingComplete")
            }
            .foregroundStyle(PanelTheme.accentIC)
        }
    }

    // MARK: - Persistence

    private func loadCurrent() {
        urlText = defaults?.string(forKey: "orchestratorURL") ?? "ws://192.168.1.50:8080/v1/session"
        tokenText = defaults?.string(forKey: "authToken") ?? "forge-dev-shared-secret"
    }

    private func save() {
        let trimmedURL = urlText.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedToken = tokenText.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmedURL.isEmpty {
            defaults?.set(trimmedURL, forKey: "orchestratorURL")
        }
        if !trimmedToken.isEmpty {
            defaults?.set(trimmedToken, forKey: "authToken")
        }
    }
}
