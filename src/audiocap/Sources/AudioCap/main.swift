import Foundation
import CoreAudio
import AudioToolbox
import AVFoundation

// =============================================================================
// audiocap - Capture system audio + microphone via Core Audio Taps
//
// Usage:
//   audiocap --output <path.wav>
//   Stop with Ctrl+C (SIGINT)
//
// Architecture:
//   1. Create a CATapDescription for system audio
//   2. Create an aggregate device combining the tap + built-in mic
//   3. Record stereo WAV: left = system, right = mic
//
// Requires: macOS 14.4+, NSAudioCaptureUsageDescription in Info.plist
// =============================================================================

// TODO: Implement Core Audio Taps capture
// Reference implementations:
//   - https://github.com/insidegui/AudioCap
//   - https://github.com/makeusabrew/audiotee
//   - https://developer.apple.com/documentation/CoreAudio/capturing-system-audio-with-core-audio-taps

print("audiocap: Core Audio Taps capture")
print("TODO: This is a skeleton. Implement based on AudioCap or AudioTee.")
print("See CLAUDE.md for architecture details.")

// Placeholder: keep running until SIGINT
let semaphore = DispatchSemaphore(value: 0)
signal(SIGINT) { _ in
    print("\naudiocap: Stopping recording...")
    semaphore.signal()
}
print("audiocap: Waiting for SIGINT to stop...")
semaphore.wait()
