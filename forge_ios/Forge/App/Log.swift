import os

// Centralized loggers. View in Console.app (or `xcrun simctl spawn booted log
// stream`) filtered to subsystem `ai.forge.ios`. Categories let you isolate a
// subsystem: e.g. `subsystem:ai.forge.ios category:net`.
enum Log {
    static let net = Logger(subsystem: "ai.forge.ios", category: "net")
    static let session = Logger(subsystem: "ai.forge.ios", category: "session")
    static let chat = Logger(subsystem: "ai.forge.ios", category: "chat")
    static let ar = Logger(subsystem: "ai.forge.ios", category: "ar")
}
