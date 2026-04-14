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
        fputs("audiocap: Tap created (ID: \(tapID))\n", stderr)

        // Read the tap's UID
        let tapUID = try getStringProperty(
            objectID: tapID,
            selector: kAudioTapPropertyUID
        )
        fputs("audiocap: Tap UID: \(tapUID)\n", stderr)

        // Read the tap's format
        debugTapFormat()

        // Step 2: Get the default input device (microphone) UID
        let micDeviceID = try getDefaultInputDevice()
        let micUID = try getStringProperty(
            objectID: micDeviceID,
            selector: kAudioDevicePropertyDeviceUID
        )
        fputs("audiocap: Mic device ID: \(micDeviceID), UID: \(micUID)\n", stderr)

        // Step 3: Create aggregate device combining tap + mic
        let aggregateUID = UUID().uuidString
        let aggregateDescription: [String: Any] = [
            kAudioAggregateDeviceNameKey as String: "audiocap",
            kAudioAggregateDeviceUIDKey as String: aggregateUID,
            kAudioAggregateDeviceIsPrivateKey as String: 1,
            kAudioAggregateDeviceSubDeviceListKey as String: [micUID],
            kAudioAggregateDeviceTapListKey as String: [[
                kAudioSubTapUIDKey as String: tapUID,
            ] as [String: Any]],
            kAudioAggregateDeviceTapAutoStartKey as String: 1,
        ]

        fputs("audiocap: Creating aggregate device...\n", stderr)
        var aggregateObjectID: AudioObjectID = kAudioObjectUnknown
        let aggStatus = AudioHardwareCreateAggregateDevice(
            aggregateDescription as CFDictionary,
            &aggregateObjectID
        )
        guard aggStatus == noErr else {
            throw AudioCapError.aggregateCreationFailed(aggStatus)
        }
        self.aggregateID = aggregateObjectID
        fputs("audiocap: Aggregate device created (ID: \(aggregateID))\n", stderr)

        // Debug: check streams on aggregate
        debugAggregateStreams()
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

    // MARK: - Debug

    private func debugTapFormat() {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioTapPropertyFormat,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var format = AudioStreamBasicDescription()
        var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        let status = AudioObjectGetPropertyData(tapID, &address, 0, nil, &size, &format)
        if status == noErr {
            fputs("audiocap: Tap format: \(format.mSampleRate) Hz, \(format.mChannelsPerFrame) ch, \(format.mBitsPerChannel) bit\n", stderr)
            fputs("audiocap: Tap format flags: \(format.mFormatFlags), bytesPerFrame: \(format.mBytesPerFrame)\n", stderr)
        } else {
            fputs("audiocap: Could not read tap format (OSStatus \(status))\n", stderr)
        }
    }

    private func debugAggregateStreams() {
        // Count input streams
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyStreams,
            mScope: kAudioObjectPropertyScopeInput,
            mElement: kAudioObjectPropertyElementMain
        )
        var size: UInt32 = 0
        var status = AudioObjectGetPropertyDataSize(aggregateID, &address, 0, nil, &size)
        let inputStreamCount = status == noErr ? Int(size) / MemoryLayout<AudioStreamID>.size : 0
        fputs("audiocap: Aggregate input streams: \(inputStreamCount)\n", stderr)

        // Count output streams
        address.mScope = kAudioObjectPropertyScopeOutput
        status = AudioObjectGetPropertyDataSize(aggregateID, &address, 0, nil, &size)
        let outputStreamCount = status == noErr ? Int(size) / MemoryLayout<AudioStreamID>.size : 0
        fputs("audiocap: Aggregate output streams: \(outputStreamCount)\n", stderr)

        // Read input stream format if available
        if inputStreamCount > 0 {
            var streamFormat = AudioStreamBasicDescription()
            var fmtSize = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
            var fmtAddress = AudioObjectPropertyAddress(
                mSelector: kAudioDevicePropertyStreamFormat,
                mScope: kAudioObjectPropertyScopeInput,
                mElement: kAudioObjectPropertyElementMain
            )
            status = AudioObjectGetPropertyData(aggregateID, &fmtAddress, 0, nil, &fmtSize, &streamFormat)
            if status == noErr {
                fputs("audiocap: Aggregate input format: \(streamFormat.mSampleRate) Hz, \(streamFormat.mChannelsPerFrame) ch\n", stderr)
            }
        }
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
        var status = AudioObjectGetPropertyDataSize(objectID, &address, 0, nil, &size)
        guard status == noErr else {
            throw AudioCapError.propertyReadFailed(selector, status)
        }
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
