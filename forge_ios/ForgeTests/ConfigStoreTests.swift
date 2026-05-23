import XCTest
@testable import Forge

// WITHOUT BACKEND — backend-pointing config resolution. The single source for
// "which backend" is ConfigStore (env → UserDefaults suite → default).
final class ConfigStoreTests: XCTestCase {

    private let suite = UserDefaults(suiteName: "ai.forge.settings")!
    private var envURLSet: Bool { ProcessInfo.processInfo.environment["ORCHESTRATOR_URL"] != nil }

    override func tearDown() {
        suite.removeObject(forKey: "orchestratorURL")
        suite.removeObject(forKey: "authToken")
        super.tearDown()
    }

    func testDefaultPointsAtV2Chat() throws {
        try XCTSkipIf(envURLSet, "ORCHESTRATOR_URL env override is set")
        suite.removeObject(forKey: "orchestratorURL")
        suite.removeObject(forKey: "authToken")
        let cfg = ConfigStore.load()
        XCTAssertEqual(cfg.orchestratorURL.absoluteString, "ws://192.168.1.50:8080/v2/chat")
        XCTAssertEqual(cfg.authToken, "forge-dev-shared-secret")
    }

    func testUserDefaultsOverridesDefault() throws {
        try XCTSkipIf(envURLSet, "ORCHESTRATOR_URL env override is set")
        suite.set("ws://10.0.0.5:9000/v2/chat", forKey: "orchestratorURL")
        suite.set("my-token", forKey: "authToken")
        let cfg = ConfigStore.load()
        XCTAssertEqual(cfg.orchestratorURL.absoluteString, "ws://10.0.0.5:9000/v2/chat")
        XCTAssertEqual(cfg.authToken, "my-token")
    }
}
