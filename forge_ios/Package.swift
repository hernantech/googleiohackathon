// swift-tools-version:5.10
// Forge iOS ships no third-party SPM dependencies in v1. This manifest exists
// so the directory is recognizable as a package root and so Phase-7 deps have a
// home; the app target itself is built from Forge.xcodeproj (generated via
// `xcodegen` from project.yml).
import PackageDescription

let package = Package(
    name: "Forge",
    platforms: [.iOS(.v17)],
    products: [],
    dependencies: [],
    targets: []
)
