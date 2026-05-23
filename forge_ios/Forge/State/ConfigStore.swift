import Foundation

struct ConfigStore {
    var orchestratorURL: URL
    var authToken: String

    static func load() -> ConfigStore {
        let defaults = UserDefaults(suiteName: "ai.forge.ios")

        let rawURL: String = {
            if let env = ProcessInfo.processInfo.environment["ORCHESTRATOR_URL"], !env.isEmpty {
                return env
            }
            if let stored = defaults?.string(forKey: "orchestratorURL"), !stored.isEmpty {
                return stored
            }
            return "ws://192.168.1.50:8080/v2/chat"
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

        let url = URL(string: rawURL) ?? URL(string: "ws://192.168.1.50:8080/v2/chat")!
        return ConfigStore(orchestratorURL: url, authToken: token)
    }
}
