import XCTest
@testable import Forge

// WITHOUT BACKEND — backend-pointing config resolution. The single source for
// "which backend" is ConfigStore (env → UserDefaults suite → default).
//
// Phase 6 note: the default is now the live backend at http://20.230.188.247:8080
// (ROADMAP.md §Current state).  ConfigStore derives three URLs from one base:
//   orchestratorURL  → WSS /v2/chat
//   liveURL          → WSS /v2/live
//   snapshotBaseURL  → HTTP base
final class ConfigStoreTests: XCTestCase {

    private let suite = UserDefaults(suiteName: "ai.forge.settings")!
    private var envOverrideSet: Bool {
        ProcessInfo.processInfo.environment["ORCHESTRATOR_BASE"] != nil ||
        ProcessInfo.processInfo.environment["ORCHESTRATOR_URL"] != nil
    }

    override func tearDown() {
        suite.removeObject(forKey: "orchestratorBase")
        suite.removeObject(forKey: "orchestratorURL")
        suite.removeObject(forKey: "authToken")
        super.tearDown()
    }

    func testDefaultPointsAtLiveBackend() throws {
        try XCTSkipIf(envOverrideSet, "ORCHESTRATOR_BASE/URL env override is set")
        suite.removeObject(forKey: "orchestratorBase")
        suite.removeObject(forKey: "orchestratorURL")
        suite.removeObject(forKey: "authToken")
        let cfg = ConfigStore.load()
        // Default live backend (ROADMAP.md §Current state).
        XCTAssertTrue(cfg.orchestratorURL.absoluteString.contains("/v2/chat"),
                      "chat URL must end with /v2/chat: \(cfg.orchestratorURL)")
        XCTAssertTrue(cfg.liveURL.absoluteString.contains("/v2/live"),
                      "live URL must end with /v2/live: \(cfg.liveURL)")
        XCTAssertEqual(cfg.authToken, "forge-dev-shared-secret")
    }

    func testUserDefaultsOverridesDefault() throws {
        try XCTSkipIf(envOverrideSet, "ORCHESTRATOR_BASE/URL env override is set")
        // New-style key: orchestratorBase (http base URL).
        suite.set("http://10.0.0.5:9000", forKey: "orchestratorBase")
        suite.set("my-token", forKey: "authToken")
        let cfg = ConfigStore.load()
        XCTAssertEqual(cfg.orchestratorURL.absoluteString, "ws://10.0.0.5:9000/v2/chat")
        XCTAssertEqual(cfg.liveURL.absoluteString, "ws://10.0.0.5:9000/v2/live")
        XCTAssertEqual(cfg.snapshotBaseURL.absoluteString, "http://10.0.0.5:9000")
        XCTAssertEqual(cfg.authToken, "my-token")
    }

    func testLegacyOrchestratorURLKeyStillLoaded() throws {
        try XCTSkipIf(envOverrideSet, "ORCHESTRATOR_BASE/URL env override is set")
        // Legacy key: full WS URL written by older app versions.
        suite.set("ws://192.168.1.50:8080/v2/chat", forKey: "orchestratorURL")
        let cfg = ConfigStore.load()
        // The WS scheme is detected and the /v2/chat path stripped when deriving the base.
        XCTAssertTrue(cfg.orchestratorURL.absoluteString.contains("192.168.1.50"),
                      "should carry over legacy host: \(cfg.orchestratorURL)")
    }
}
