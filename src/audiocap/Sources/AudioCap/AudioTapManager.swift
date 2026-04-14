import AudioToolbox
import CoreAudio
import Foundation

/// Manages Core Audio Tap creation and aggregate device setup.
/// Based on the working pattern from obsfx/audiograb.
final class AudioTapManager {
    private(set) var tapID: AudioObjectID = kAudioObjectUnknown
    private(set) var aggregateID: AudioObjectID = kAudioObjectUnknown
    private(set) var tapFormat: AudioStreamBasicDescription?

    /// Create the system audio tap and aggregate device.
    func create() throws {
        // Step 1: Create a tap that captures all system audio
        let tapDescription = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
        tapDescription.uuid = UUID()
        tapDescription.name = "audiocap-tap"
        tapDescription.isPrivate = true
        tapDescription.isMixdown = true
        tapDescription.muteBehavior = .unmuted

        var tapObjectID: AudioObjectID = AudioObjectID(kAudioObjectUnknown)
        let tapStatus = AudioHardwareCreateProcessTap(tapDescription, &tapObjectID)
        guard tapStatus == kAudioHardwareNoError else {
            throw AudioCapError.tapCreationFailed(tapStatus)
        }
        self.tapID = tapObjectID
        fputs("audiocap: Tap created (ID: \(tapID))\n", stderr)

        // Read the tap's format
        self.tapFormat = try queryTapFormat()
        if let fmt = tapFormat {
            fputs("audiocap: Tap format: \(fmt.mSampleRate) Hz, \(fmt.mChannelsPerFrame) ch, \(fmt.mBitsPerChannel) bit\n", stderr)
        }

        // Read the tap's UID
        let tapUID = try getTapUID()
        fputs("audiocap: Tap UID: \(tapUID)\n", stderr)

        // Step 2: Create aggregate device with tap only (no sub-devices needed)
        let aggregateUID = UUID().uuidString
        let tapEntry: [String: Any] = [
            kAudioSubTapUIDKey: tapUID,
            kAudioSubTapDriftCompensationKey: false,
        ]
        let description: [String: Any] = [
            kAudioAggregateDeviceNameKey: "audiocap-device",
            kAudioAggregateDeviceUIDKey: aggregateUID,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceTapListKey: [tapEntry],
            kAudioAggregateDeviceTapAutoStartKey: false,
        ]

        fputs("audiocap: Creating aggregate device...\n", stderr)
        var aggregateObjectID: AudioObjectID = 0
        let aggStatus = AudioHardwareCreateAggregateDevice(
            description as CFDictionary, &aggregateObjectID
        )
        guard aggStatus == kAudioHardwareNoError else {
            throw AudioCapError.aggregateCreationFailed(aggStatus)
        }
        self.aggregateID = aggregateObjectID
        fputs("audiocap: Aggregate device created (ID: \(aggregateID))\n", stderr)
    }

    /// Destroy the aggregate device and tap. Call during shutdown.
    func destroy() {
        if aggregateID != kAudioObjectUnknown {
            AudioHardwareDestroyAggregateDevice(aggregateID)
            aggregateID = kAudioObjectUnknown
        }
        if tapID != kAudioObjectUnknown {
            AudioHardwareDestroyProcessTap(tapID)
            tapID = kAudioObjectUnknown
        }
    }

    deinit { destroy() }

    // MARK: - Private

    private func queryTapFormat() throws -> AudioStreamBasicDescription {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioTapPropertyFormat,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.stride)
        var format = AudioStreamBasicDescription()
        let status = AudioObjectGetPropertyData(tapID, &address, 0, nil, &size, &format)
        guard status == noErr else {
            throw AudioCapError.propertyReadFailed(kAudioTapPropertyFormat, status)
        }
        return format
    }

    private func getTapUID() throws -> String {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioTapPropertyUID,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var size = UInt32(MemoryLayout<CFString>.stride)
        var tapUID: CFString = "" as CFString
        let status = withUnsafeMutablePointer(to: &tapUID) { ptr in
            AudioObjectGetPropertyData(tapID, &address, 0, nil, &size, ptr)
        }
        guard status == noErr else {
            throw AudioCapError.propertyReadFailed(kAudioTapPropertyUID, status)
        }
        return tapUID as String
    }
}

enum AudioCapError: Error, CustomStringConvertible {
    case tapCreationFailed(OSStatus)
    case aggregateCreationFailed(OSStatus)
    case noInputDevice
    case propertyReadFailed(AudioObjectPropertySelector, OSStatus)
    case ioProcFailed(OSStatus)
    case deviceStartFailed(OSStatus)
    case fileCreationFailed(String)

    var description: String {
        switch self {
        case .tapCreationFailed(let s): "Failed to create audio tap (OSStatus \(s))"
        case .aggregateCreationFailed(let s): "Failed to create aggregate device (OSStatus \(s))"
        case .noInputDevice: "No default input device (microphone) found"
        case .propertyReadFailed(let sel, let s): "Failed to read property \(sel) (OSStatus \(s))"
        case .ioProcFailed(let s): "Failed to create IOProc (OSStatus \(s))"
        case .deviceStartFailed(let s): "Failed to start audio device (OSStatus \(s))"
        case .fileCreationFailed(let path): "Failed to create audio file at \(path)"
        }
    }
}
