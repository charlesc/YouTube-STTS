// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "apple-llm-bridge",
    platforms: [.macOS("26.0")],
    targets: [
        .executableTarget(
            name: "apple-llm-bridge",
            path: "Sources/apple-llm-bridge"
        )
    ]
)
