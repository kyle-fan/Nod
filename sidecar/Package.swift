// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "NodSidecar",
    platforms: [.macOS(.v15)],
    dependencies: [
        .package(url: "https://github.com/argmaxinc/argmax-oss-swift.git", from: "1.1.1"),
        .package(url: "https://github.com/swhitty/FlyingFox.git", from: "0.27.0"),
    ],
    targets: [
        .executableTarget(
            name: "NodSidecar",
            dependencies: [
                .product(name: "WhisperKit", package: "argmax-oss-swift"),
                .product(name: "TTSKit", package: "argmax-oss-swift"),
                .product(name: "FlyingFox", package: "FlyingFox"),
            ],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
    ]
)
