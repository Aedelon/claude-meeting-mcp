import CoreAudio
import Foundation

/// Manages Core Audio Tap creation and aggregate device setup.
/// Captures all system audio + default microphone input.
final class AudioTapManager {
    private(set) var tapID: AudioObjectID = kAudioObjectUnknown
    private(set) var aggregateID: AudioObjectID = kAudioObjectUnknown

    /// Create the system audio tap and aggregate device with microphone.
    func create() throws {
        // Step 1: Create a tap that captures all system audio
        let tapDescription = CATapDescription(stereoMixdownOfProcesses: [])
        tapDescription.uuid = UUID()
        tapDescription.name = "audiocap-tap"

        var tapObjectID: AudioObjectID = kAudioObjectUnknown
        let tapStatus = AudioHardwareCreateProcessTap(tapDescription, &tapObjectID)
        guard tapStatus == noErr else {
            throw AudioCapError.tapCreationFailed(tapStatus)
        }
        self.tapID = tapObjectID

        // Read the tap's UID
        let tapUID = try getStringProperty(
            objectID: tapID,
            selector: kAudioTapPropertyUID
        )

        // Step 2: Get the default input device (microphone) UID
        let micDeviceID = try getDefaultInputDevice()
        let micUID = try getStringProperty(
            objectID: micDeviceID,
            selector: kAudioDevicePropertyDeviceUID
        )

        // Step 3: Create aggregate device combining tap + mic
        let aggregateUID = UUID().uuidString
        let aggregateDescription: [String: Any] = [
            kAudioAggregateDeviceNameKey as String: "audiocap" as CFString,
            kAudioAggregateDeviceUIDKey as String: aggregateUID as CFString,
            kAudioAggregateDeviceIsPrivateKey as String: true,
            kAudioAggregateDeviceSubDeviceListKey as String: [micUID],
            kAudioAggregateDeviceTapListKey as String: [[
                kAudioSubTapUIDKey as String: tapUID,
                kAudioSubTapDriftCompensationKey as String: true,
            ] as [String: Any]],
            kAudioAggregateDeviceTapAutoStartKey as String: true,
        ]

        var aggregateObjectID: AudioObjectID = kAudioObjectUnknown
        let aggStatus = AudioHardwareCreateAggregateDevice(
            aggregateDescription as CFDictionary,
            &aggregateObjectID
        )
        guard aggStatus == noErr else {
            throw AudioCapError.aggregateCreationFailed(aggStatus)
        }
        self.aggregateID = aggregateObjectID
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

    deinit {
        destroy()
    }

    // MARK: - Helpers

    private func getDefaultInputDevice() throws -> AudioObjectID {
        var deviceID: AudioObjectID = kAudioObjectUnknown
        var size = UInt32(MemoryLayout<AudioObjectID>.size)
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultInputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        let status = AudioObjectGetPropertyData(
            AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &deviceID
        )
        guard status == noErr, deviceID != kAudioObjectUnknown else {
            throw AudioCapError.noInputDevice
        }
        return deviceID
    }

    private func getStringProperty(objectID: AudioObjectID, selector: AudioObjectPropertySelector) throws -> String {
        var address = AudioObjectPropertyAddress(
            mSelector: selector,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var size: UInt32 = 0
        // First query the size
        var status = AudioObjectGetPropertyDataSize(objectID, &address, 0, nil, &size)
        guard status == noErr else {
            throw AudioCapError.propertyReadFailed(selector, status)
        }
        // Then read the value
        let rawPtr = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: MemoryLayout<CFString>.alignment)
        defer { rawPtr.deallocate() }
        status = AudioObjectGetPropertyData(objectID, &address, 0, nil, &size, rawPtr)
        guard status == noErr else {
            throw AudioCapError.propertyReadFailed(selector, status)
        }
        let cfString = Unmanaged<CFString>.fromOpaque(rawPtr.load(as: UnsafeRawPointer.self)).takeUnretainedValue()
        return cfString as String
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
        case .tapCreationFailed(let s):
            return "Failed to create audio tap (OSStatus \(s))"
        case .aggregateCreationFailed(let s):
            return "Failed to create aggregate device (OSStatus \(s))"
        case .noInputDevice:
            return "No default input device (microphone) found"
        case .propertyReadFailed(let sel, let s):
            return "Failed to read property \(sel) (OSStatus \(s))"
        case .ioProcFailed(let s):
            return "Failed to create IOProc (OSStatus \(s))"
        case .deviceStartFailed(let s):
            return "Failed to start audio device (OSStatus \(s))"
        case .fileCreationFailed(let path):
            return "Failed to create audio file at \(path)"
        }
    }
}
