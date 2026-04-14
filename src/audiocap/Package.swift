// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "audiocap",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "audiocap",
            path: "Sources/AudioCap"
        ),
    ]
)
