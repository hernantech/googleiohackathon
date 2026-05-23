import Foundation

// MARK: - ConfigStore
//
// Centralises orchestrator endpoint configuration.  All three channels resolve
// from a single base URL / WebSocket URL:
//
//   orchestratorURL  → WSS /v2/chat   (OrchestratorSocket — chat bus)
//   liveURL          → WSS /v2/live   (LiveSocket — H.264 + audio)
//   snapshotBaseURL  → HTTP /v2/snapshot (SnapshotClient — POST JPEG)
//
// The live backend is at http://20.230.188.247:8080 (ROADMAP.md).
// Default points there so the app connects out-of-the-box without env vars.

struct ConfigStore {
    /// WebSocket URL for the v2 chat bus (OrchestratorSocket).
    var orchestratorURL: URL
    /// WebSocket URL for the always-on live stream (LiveSocket).
    var liveURL: URL
    /// HTTP base URL for the snapshot endpoint (SnapshotClient).
    var snapshotBaseURL: URL
    /// Shared-secret auth token (specs/00 §8).
    var authToken: String

    static func load() -> ConfigStore {
        let defaults = UserDefaults(suiteName: "ai.forge.settings")

        // Support both ws:// and http:// base inputs; derive the other scheme automatically.
        // Legacy key: "orchestratorURL" (ws://host/v2/chat); new key: "orchestratorBase"
        // (http://host).  If a full chat URL is stored in the legacy key, strip the path.
        let rawBase: String = {
            if let env = ProcessInfo.processInfo.environment["ORCHESTRATOR_BASE"], !env.isEmpty {
                return env
            }
            // Legacy env var used by ConfigStoreTests and existing deployments.
            if let env = ProcessInfo.processInfo.environment["ORCHESTRATOR_URL"], !env.isEmpty {
                return env
            }
            if let stored = defaults?.string(forKey: "orchestratorBase"), !stored.isEmpty {
                return stored
            }
            // Legacy UserDefaults key: may contain a full WS URL like ws://host/v2/chat.
            if let stored = defaults?.string(forKey: "orchestratorURL"), !stored.isEmpty {
                return stored
            }
            // Live backend (ROADMAP.md §Current state).
            return "http://20.230.188.247:8080"
        }()

        let token: String = {
            if let env = ProcessInfo.processInfo.environment["AUTH_TOKEN"], !env.isEmpty {
                return env
            }
            if let stored = defaults?.string(forKey: "authToken"), !stored.isEmpty {
                return stored
            }
            return "forge-dev-shared-secret"
        }()

        // Strip any /v2/* path suffix so legacy "ws://host:port/v2/chat" values
        // are normalised back to "ws://host:port" before we append our own paths.
        let strippedBase: String = {
            var s = rawBase
            for suffix in ["/v2/chat", "/v2/live", "/v2/snapshot", "/v2"] {
                if s.hasSuffix(suffix) { return String(s.dropLast(suffix.count)) }
            }
            return s
        }()

        // Derive WS URLs from the HTTP base.
        let httpBase = strippedBase
            .replacingOccurrences(of: "^wss://", with: "https://", options: .regularExpression)
            .replacingOccurrences(of: "^ws://", with: "http://", options: .regularExpression)
        let wsBase = httpBase
            .replacingOccurrences(of: "^https://", with: "wss://", options: .regularExpression)
            .replacingOccurrences(of: "^http://", with: "ws://", options: .regularExpression)

        let chatURL  = URL(string: "\(wsBase)/v2/chat")  ?? URL(string: "ws://20.230.188.247:8080/v2/chat")!
        let liveURL  = URL(string: "\(wsBase)/v2/live")  ?? URL(string: "ws://20.230.188.247:8080/v2/live")!
        let snapBase = URL(string: httpBase)              ?? URL(string: "http://20.230.188.247:8080")!

        return ConfigStore(orchestratorURL: chatURL,
                           liveURL: liveURL,
                           snapshotBaseURL: snapBase,
                           authToken: token)
    }
}
