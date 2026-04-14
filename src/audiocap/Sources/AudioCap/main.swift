import CoreAudio
import Foundation

// =============================================================================
// audiocap - Capture system audio + microphone via Core Audio Taps
//
// Usage:
//   audiocap --output <path.wav>
//   Stop with Ctrl+C (SIGINT)
//
// Output: Stereo WAV 44.1kHz 16-bit
//   Left channel  = system audio (all apps)
//   Right channel = default microphone
//
// Requires: macOS 14.4+
// =============================================================================

// MARK: - Argument Parsing

func parseOutputPath() -> String {
    let args = CommandLine.arguments
    guard let idx = args.firstIndex(of: "--output"), idx + 1 < args.count else {
        fputs("Usage: audiocap --output <path.wav>\n", stderr)
        exit(1)
    }
    return args[idx + 1]
}

let outputPath = parseOutputPath()

// MARK: - Setup

fputs("audiocap: Initializing Core Audio Taps...\n", stderr)

let tapManager = AudioTapManager()
var recorder: StereoRecorder?

do {
    try tapManager.create()
    fputs("audiocap: Tap and aggregate device created\n", stderr)

    recorder = try StereoRecorder(
        aggregateID: tapManager.aggregateID,
        outputPath: outputPath
    )
    try recorder?.start()
    fputs("audiocap: Recording to \(outputPath)\n", stderr)
} catch {
    fputs("audiocap: Error: \(error)\n", stderr)
    tapManager.destroy()
    exit(1)
}

// MARK: - SIGINT Handler

// Ignore default SIGINT behavior
signal(SIGINT, SIG_IGN)

let signalSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
signalSource.setEventHandler {
    fputs("\naudiocap: Stopping...\n", stderr)
    recorder?.stop()
    tapManager.destroy()
    fputs("audiocap: Saved to \(outputPath)\n", stderr)
    exit(0)
}
signalSource.resume()

// MARK: - Run Loop

fputs("audiocap: Press Ctrl+C to stop recording\n", stderr)
dispatchMain()
