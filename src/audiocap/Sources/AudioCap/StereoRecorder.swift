import AudioToolbox
import AVFoundation
import CoreAudio
import Foundation

/// Records stereo WAV: Left = system audio (tap), Right = microphone.
/// Uses two independent IOProcs with separate ring buffers.
final class StereoRecorder {
    // System audio (tap via aggregate device)
    private let tapDeviceID: AudioObjectID
    private let systemRing: RingBuffer
    private var systemProcID: AudioDeviceIOProcID?

    // Microphone (default input device)
    private let micDeviceID: AudioObjectID
    private let micRing: RingBuffer
    private var micProcID: AudioDeviceIOProcID?

    // Output
    private let outputPath: String
    private var audioFile: AVAudioFile?
    private let writerQueue = DispatchQueue(label: "audiocap.writer", qos: .userInitiated)
    private var writerTimer: DispatchSourceTimer?

    // Format
    private let sampleRate: Double
    private let systemChannels: Int

    // Audio processing chain: normalize → compress → limit
    // Normalization: running RMS per channel, gain to target level
    private var systemRMS: Float = 0.0
    private var micRMS: Float = 0.0
    private let rmsSmoothing: Float = 0.05
    private let maxGain: Float = 20.0
    private let targetRMS: Float = 0.1       // ~-20dB

    // Compressor: reduces dynamic range for consistent speech levels
    private let compThreshold: Float = 0.15  // ~-16dB — compress above this
    private let compRatio: Float = 4.0       // 4:1 ratio
    private var sysEnvelope: Float = 0.0     // envelope follower state
    private var micEnvelope: Float = 0.0
    private let attackCoeff: Float           // computed from sample rate
    private let releaseCoeff: Float

    // Limiter: hard ceiling to prevent clipping
    private let limiterCeiling: Float = 0.95 // ~-0.5dB

    init(
        aggregateID: AudioObjectID,
        outputPath: String,
        tapFormat: AudioStreamBasicDescription
    ) throws {
        self.tapDeviceID = aggregateID
        self.outputPath = outputPath
        self.sampleRate = tapFormat.mSampleRate
        self.systemChannels = Int(tapFormat.mChannelsPerFrame)

        // Ring buffers: ~5 seconds each
        let sysCapacity = Int(sampleRate) * systemChannels * 5 * MemoryLayout<Float>.size
        let micCapacity = Int(sampleRate) * 1 * 5 * MemoryLayout<Float>.size  // mono mic
        self.systemRing = RingBuffer(capacity: sysCapacity)
        self.micRing = RingBuffer(capacity: micCapacity)

        // Compressor envelope coefficients (attack 5ms, release 100ms)
        self.attackCoeff = expf(-1.0 / (Float(sampleRate) * 0.005))
        self.releaseCoeff = expf(-1.0 / (Float(sampleRate) * 0.100))

        // Find default input device and match sample rate to system
        self.micDeviceID = try StereoRecorder.getDefaultInputDevice()
        let micSR = StereoRecorder.getDeviceSampleRate(micDeviceID) ?? 0
        fputs("audiocap: System: \(sampleRate) Hz, \(systemChannels) ch\n", stderr)
        fputs("audiocap: Mic device: \(micDeviceID), native \(micSR) Hz\n", stderr)

        if micSR != sampleRate {
            let set = StereoRecorder.setDeviceSampleRate(micDeviceID, rate: sampleRate)
            if set {
                let newSR = StereoRecorder.getDeviceSampleRate(micDeviceID) ?? 0
                fputs("audiocap: Mic resampled to \(newSR) Hz\n", stderr)
            } else {
                fputs("audiocap: WARNING: Could not set mic to \(sampleRate) Hz, audio may drift\n", stderr)
            }
        }

        // Create WAV: stereo 16-bit at tap sample rate
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: sampleRate,
            AVNumberOfChannelsKey: 2,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
            AVLinearPCMIsNonInterleaved: false,
        ]
        do {
            self.audioFile = try AVAudioFile(
                forWriting: URL(fileURLWithPath: outputPath),
                settings: settings,
                commonFormat: .pcmFormatFloat32,
                interleaved: false
            )
        } catch {
            throw AudioCapError.fileCreationFailed("\(outputPath): \(error)")
        }
    }

    func start() throws {
        // --- System audio IOProc (on aggregate device) ---
        let sysClientData = Unmanaged.passUnretained(self.systemRing).toOpaque()
        var sysProcID: AudioDeviceIOProcID?
        var status = AudioDeviceCreateIOProcID(
            tapDeviceID,
            { (_, _, inInputData, _, _, _, inClientData) -> OSStatus in
                guard let cd = inClientData else { return noErr }
                let ring = Unmanaged<RingBuffer>.fromOpaque(cd).takeUnretainedValue()
                let buf = inInputData.pointee.mBuffers
                guard let data = buf.mData, buf.mDataByteSize > 0 else { return noErr }
                ring.writeBytes(data, count: Int(buf.mDataByteSize))
                return noErr
            },
            sysClientData,
            &sysProcID
        )
        guard status == noErr, let sysProcID else {
            throw AudioCapError.ioProcFailed(status)
        }
        self.systemProcID = sysProcID

        status = AudioDeviceStart(tapDeviceID, sysProcID)
        guard status == noErr else {
            throw AudioCapError.deviceStartFailed(status)
        }
        fputs("audiocap: System IOProc started\n", stderr)

        // --- Microphone IOProc (on default input device) ---
        let micClientData = Unmanaged.passUnretained(self.micRing).toOpaque()
        var mProcID: AudioDeviceIOProcID?
        status = AudioDeviceCreateIOProcID(
            micDeviceID,
            { (_, _, inInputData, _, _, _, inClientData) -> OSStatus in
                guard let cd = inClientData else { return noErr }
                let ring = Unmanaged<RingBuffer>.fromOpaque(cd).takeUnretainedValue()
                let buf = inInputData.pointee.mBuffers
                guard let data = buf.mData, buf.mDataByteSize > 0 else { return noErr }
                // Mic may be multi-channel but we only take first channel's worth
                ring.writeBytes(data, count: Int(buf.mDataByteSize))
                return noErr
            },
            micClientData,
            &mProcID
        )
        guard status == noErr, let mProcID else {
            throw AudioCapError.ioProcFailed(status)
        }
        self.micProcID = mProcID

        status = AudioDeviceStart(micDeviceID, mProcID)
        if status != noErr {
            fputs("audiocap: Mic start failed (\(status)), recording system audio only\n", stderr)
            AudioDeviceDestroyIOProcID(micDeviceID, mProcID)
            self.micProcID = nil
        } else {
            fputs("audiocap: Mic IOProc started\n", stderr)
        }

        startWriterTimer()
        fputs("audiocap: Recording started\n", stderr)
    }

    func stop() {
        writerTimer?.cancel()
        writerTimer = nil

        if let procID = systemProcID {
            AudioDeviceStop(tapDeviceID, procID)
            AudioDeviceDestroyIOProcID(tapDeviceID, procID)
            systemProcID = nil
        }
        if let procID = micProcID {
            AudioDeviceStop(micDeviceID, procID)
            AudioDeviceDestroyIOProcID(micDeviceID, procID)
            micProcID = nil
        }

        // Final drain
        writerQueue.sync { self.drainBuffers() }

        audioFile = nil
        fputs("audiocap: Recording stopped\n", stderr)
    }

    // MARK: - Writer

    private func startWriterTimer() {
        let timer = DispatchSource.makeTimerSource(queue: writerQueue)
        timer.schedule(deadline: .now(), repeating: .milliseconds(10))
        timer.setEventHandler { [weak self] in
            self?.drainBuffers()
        }
        timer.resume()
        writerTimer = timer
    }

    private func drainBuffers() {
        guard let audioFile else { return }

        let float32Size = MemoryLayout<Float32>.stride
        let sysBytesPerFrame = systemChannels * float32Size
        let micBytesPerSample = float32Size  // mono

        // Read system audio
        let sysAvailable = systemRing.availableBytesToRead
        guard sysAvailable > 0 else { return }

        let maxFrames = 4096
        let maxSysBytes = maxFrames * sysBytesPerFrame
        let maxMicBytes = maxFrames * micBytesPerSample

        let sysBuffer = UnsafeMutableRawPointer.allocate(byteCount: maxSysBytes, alignment: 16)
        let micBuffer = UnsafeMutableRawPointer.allocate(byteCount: maxMicBytes, alignment: 16)
        defer {
            sysBuffer.deallocate()
            micBuffer.deallocate()
        }

        var sysRemaining = sysAvailable
        while sysRemaining > 0 {
            let toReadSys = min(sysRemaining, maxSysBytes)
            let alignedSys = (toReadSys / sysBytesPerFrame) * sysBytesPerFrame
            guard alignedSys > 0 else { break }

            let sysRead = systemRing.readBytes(sysBuffer, count: alignedSys)
            guard sysRead > 0 else { break }

            let frameCount = sysRead / sysBytesPerFrame

            // Read matching amount of mic data (best effort — may have less)
            let wantMicBytes = frameCount * micBytesPerSample
            let micRead = micRing.readBytes(micBuffer, count: wantMicBytes)
            let micFrames = micRead / micBytesPerSample

            let sysPtr = sysBuffer.assumingMemoryBound(to: Float32.self)
            let micPtr = micBuffer.assumingMemoryBound(to: Float32.self)

            // Write stereo: L = system (mono mixdown), R = mic
            guard let pcmBuffer = AVAudioPCMBuffer(
                pcmFormat: audioFile.processingFormat,
                frameCapacity: AVAudioFrameCount(frameCount)
            ) else { break }

            pcmBuffer.frameLength = AVAudioFrameCount(frameCount)
            guard let channelData = pcmBuffer.floatChannelData else { break }

            let leftCh = channelData[0]
            let rightCh = channelData[1]

            // === Step 1: Extract raw samples ===
            var sysSum: Float = 0.0
            var micSum: Float = 0.0

            for f in 0..<frameCount {
                let sysVal: Float
                if systemChannels >= 2 {
                    sysVal = (sysPtr[f * systemChannels] + sysPtr[f * systemChannels + 1]) * 0.5
                } else {
                    sysVal = sysPtr[f]
                }
                leftCh[f] = sysVal
                sysSum += sysVal * sysVal

                let micVal: Float = f < micFrames ? micPtr[f] : 0.0
                rightCh[f] = micVal
                micSum += micVal * micVal
            }

            // === Step 2: Normalize — bring both channels to target RMS ===
            let chunkSysRMS = sqrtf(sysSum / Float(max(frameCount, 1)))
            let chunkMicRMS = sqrtf(micSum / Float(max(micFrames, 1)))
            systemRMS += (chunkSysRMS - systemRMS) * rmsSmoothing
            micRMS += (chunkMicRMS - micRMS) * rmsSmoothing

            let sysGain = systemRMS > 0.001 ? min(targetRMS / systemRMS, maxGain) : 1.0
            let micGain = micRMS > 0.001 ? min(targetRMS / micRMS, maxGain) : 1.0

            for f in 0..<frameCount {
                leftCh[f] *= sysGain
                rightCh[f] *= micGain
            }

            // === Step 3: Compress — reduce dynamic range (4:1 above threshold) ===
            for f in 0..<frameCount {
                leftCh[f] = compressSample(leftCh[f], envelope: &sysEnvelope)
                rightCh[f] = compressSample(rightCh[f], envelope: &micEnvelope)
            }

            // === Step 4: Limit — hard ceiling at -0.5dB ===
            for f in 0..<frameCount {
                leftCh[f] = max(-limiterCeiling, min(limiterCeiling, leftCh[f]))
                rightCh[f] = max(-limiterCeiling, min(limiterCeiling, rightCh[f]))
            }

            do {
                try audioFile.write(from: pcmBuffer)
            } catch {
                fputs("audiocap: Write error: \(error)\n", stderr)
                break
            }

            sysRemaining -= sysRead
        }
    }

    // MARK: - Audio Processing

    /// Soft-knee compressor: 4:1 ratio above threshold, with envelope follower.
    private func compressSample(_ sample: Float, envelope: inout Float) -> Float {
        let absSample = abs(sample)

        // Envelope follower (fast attack, slow release)
        if absSample > envelope {
            envelope = attackCoeff * envelope + (1.0 - attackCoeff) * absSample
        } else {
            envelope = releaseCoeff * envelope + (1.0 - releaseCoeff) * absSample
        }

        // Compute gain reduction
        if envelope > compThreshold {
            let overDB = 20.0 * log10f(envelope / compThreshold)
            let reducedDB = overDB / compRatio
            let targetLevel = compThreshold * powf(10.0, reducedDB / 20.0)
            let gain = targetLevel / envelope
            return sample * gain
        }

        return sample
    }

    // MARK: - Helpers

    private static func getDefaultInputDevice() throws -> AudioObjectID {
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

    private static func getDeviceSampleRate(_ deviceID: AudioObjectID) -> Double? {
        var rate: Float64 = 0
        var size = UInt32(MemoryLayout<Float64>.size)
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyNominalSampleRate,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        let status = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &rate)
        return status == noErr ? rate : nil
    }

    /// Set the nominal sample rate on a device. Core Audio HAL handles resampling internally.
    @discardableResult
    private static func setDeviceSampleRate(_ deviceID: AudioObjectID, rate: Double) -> Bool {
        var newRate: Float64 = rate
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyNominalSampleRate,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        let size = UInt32(MemoryLayout<Float64>.size)
        let status = AudioObjectSetPropertyData(deviceID, &address, 0, nil, size, &newRate)
        return status == noErr
    }
}
