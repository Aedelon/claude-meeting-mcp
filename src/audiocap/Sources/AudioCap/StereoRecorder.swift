import AVFoundation
import CoreAudio
import Foundation

/// Records stereo WAV from an aggregate device (tap + mic).
/// Left channel = system audio (tap), Right channel = microphone.
final class StereoRecorder {
    private let aggregateID: AudioObjectID
    private let outputURL: URL

    private var ioProcID: AudioDeviceIOProcID?
    private var audioFile: AVAudioFile?
    private let ringBuffer: RingBuffer
    private let writerQueue = DispatchQueue(label: "audiocap.writer")
    private var isRunning = false
    private var writerTimer: DispatchSourceTimer?
    private var callbackCount: Int = 0
    private let sampleRate: Double

    /// Ring buffer holds ~5 seconds of stereo float data
    private let ringCapacity: Int

    init(aggregateID: AudioObjectID, outputPath: String) throws {
        self.aggregateID = aggregateID
        self.outputURL = URL(fileURLWithPath: outputPath)

        // Read the actual sample rate from the aggregate device
        self.sampleRate = StereoRecorder.getDeviceSampleRate(aggregateID) ?? 48000.0
        fputs("audiocap: Using sample rate: \(sampleRate)\n", stderr)

        self.ringCapacity = Int(sampleRate) * 2 * 5  // 5 sec of stereo floats
        self.ringBuffer = RingBuffer(capacity: ringCapacity)

        // Create WAV file matching the aggregate's actual sample rate
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
                forWriting: outputURL,
                settings: settings,
                commonFormat: .pcmFormatFloat32,
                interleaved: false
            )
        } catch {
            throw AudioCapError.fileCreationFailed("\(outputPath): \(error)")
        }
    }

    func start() throws {
        // Use block-based IOProc (works correctly with aggregate + tap devices)
        var procID: AudioDeviceIOProcID?

        let status = AudioDeviceCreateIOProcIDWithBlock(
            &procID,
            aggregateID,
            nil  // Use default dispatch queue
        ) { [weak self] (
            _: UnsafePointer<AudioTimeStamp>,
            inInputData: UnsafePointer<AudioBufferList>,
            _: UnsafePointer<AudioTimeStamp>,
            outOutputData: UnsafeMutablePointer<AudioBufferList>,
            _: UnsafePointer<AudioTimeStamp>
        ) in
            self?.handleIOProc(inInputData: inInputData)
        }

        guard status == noErr, let procID = procID else {
            fputs("audiocap: IOProc creation failed: \(status)\n", stderr)
            throw AudioCapError.ioProcFailed(status)
        }
        self.ioProcID = procID
        fputs("audiocap: IOProc (block) created on aggregate device \(aggregateID)\n", stderr)

        // Start the device
        let startStatus = AudioDeviceStart(aggregateID, procID)
        fputs("audiocap: AudioDeviceStart returned: \(startStatus)\n", stderr)
        guard startStatus == noErr else {
            throw AudioCapError.deviceStartFailed(startStatus)
        }
        isRunning = true

        // Start writer timer that drains ring buffer to disk
        startWriterTimer()

        fputs("audiocap: Recording started\n", stderr)
    }

    func stop() {
        guard isRunning else { return }
        isRunning = false

        // Stop the device
        if let procID = ioProcID {
            AudioDeviceStop(aggregateID, procID)
            AudioDeviceDestroyIOProcID(aggregateID, procID)
            ioProcID = nil
        }

        // Stop writer timer and flush remaining data
        writerTimer?.cancel()
        writerTimer = nil

        writerQueue.sync {
            self.flushRingBuffer()
        }

        fputs("audiocap: \(callbackCount) IOProc callbacks processed\n", stderr)
        fputs("audiocap: Ring buffer remaining: \(ringBuffer.availableToRead) samples\n", stderr)

        audioFile = nil
        fputs("audiocap: Recording stopped\n", stderr)
    }

    // MARK: - IOProc Handler

    private func handleIOProc(inInputData: UnsafePointer<AudioBufferList>) {
        callbackCount += 1

        let abl = UnsafeMutableAudioBufferListPointer(
            UnsafeMutablePointer(mutating: inInputData)
        )

        // Debug: log first callback info
        if callbackCount == 1 {
            fputs("audiocap: IOProc first callback - \(abl.count) buffers\n", stderr)
            for (i, buf) in abl.enumerated() {
                fputs("audiocap:   buffer[\(i)]: \(buf.mNumberChannels) ch, \(buf.mDataByteSize) bytes\n", stderr)
            }
        }

        // Extract buffers: first = system tap, second = mic
        var systemPtr: UnsafeMutablePointer<Float>?
        var systemFrames: Int = 0
        var systemChannels: Int = 0
        var micPtr: UnsafeMutablePointer<Float>?
        var micFrames: Int = 0

        for (index, buf) in abl.enumerated() {
            guard let data = buf.mData else { continue }
            let channels = Int(buf.mNumberChannels)
            guard channels > 0, buf.mDataByteSize > 0 else { continue }
            let frameCount = Int(buf.mDataByteSize) / (MemoryLayout<Float>.size * channels)

            if index == 0 {
                systemPtr = data.assumingMemoryBound(to: Float.self)
                systemFrames = frameCount
                systemChannels = channels
            } else if micPtr == nil {
                micPtr = data.assumingMemoryBound(to: Float.self)
                micFrames = frameCount
            }
        }

        // Determine frame count
        let frames: Int
        if systemPtr != nil && micPtr != nil {
            frames = min(systemFrames, micFrames)
        } else if systemPtr != nil {
            frames = systemFrames
        } else {
            return  // No data
        }
        guard frames > 0 else { return }

        // Interleave: L = system, R = mic
        let stereoCount = frames * 2
        let stereo = UnsafeMutablePointer<Float>.allocate(capacity: stereoCount)
        defer { stereo.deallocate() }

        for i in 0..<frames {
            // Left: system audio (mono mixdown if stereo)
            if let sys = systemPtr {
                if systemChannels >= 2 {
                    stereo[i * 2] = (sys[i * systemChannels] + sys[i * systemChannels + 1]) * 0.5
                } else {
                    stereo[i * 2] = sys[i]
                }
            } else {
                stereo[i * 2] = 0.0
            }

            // Right: mic (or silence)
            if let mic = micPtr {
                stereo[i * 2 + 1] = mic[i]
            } else {
                stereo[i * 2 + 1] = 0.0
            }
        }

        ringBuffer.write(stereo, count: stereoCount)
    }

    // MARK: - File Writer

    private func startWriterTimer() {
        let timer = DispatchSource.makeTimerSource(queue: writerQueue)
        timer.schedule(deadline: .now() + .milliseconds(200), repeating: .milliseconds(100))
        timer.setEventHandler { [weak self] in
            self?.flushRingBuffer()
        }
        timer.resume()
        self.writerTimer = timer
    }

    private func flushRingBuffer() {
        let available = ringBuffer.availableToRead
        guard available >= 2, let audioFile = audioFile else { return }

        let toRead = available - (available % 2)
        guard toRead > 0 else { return }

        let frameCount = toRead / 2

        let interleavedBuf = UnsafeMutablePointer<Float>.allocate(capacity: toRead)
        defer { interleavedBuf.deallocate() }
        ringBuffer.read(into: interleavedBuf, count: toRead)

        guard let buffer = AVAudioPCMBuffer(
            pcmFormat: audioFile.processingFormat,
            frameCapacity: AVAudioFrameCount(frameCount)
        ) else { return }

        buffer.frameLength = AVAudioFrameCount(frameCount)

        guard let channelData = buffer.floatChannelData else { return }
        let leftChannel = channelData[0]
        let rightChannel = channelData[1]

        for i in 0..<frameCount {
            leftChannel[i] = interleavedBuf[i * 2]
            rightChannel[i] = interleavedBuf[i * 2 + 1]
        }

        do {
            try audioFile.write(from: buffer)
        } catch {
            fputs("audiocap: Write error: \(error)\n", stderr)
        }
    }

    // MARK: - Helpers

    private static func getDeviceSampleRate(_ deviceID: AudioObjectID) -> Double? {
        var sampleRate: Float64 = 0
        var size = UInt32(MemoryLayout<Float64>.size)
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyNominalSampleRate,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        let status = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &sampleRate)
        return status == noErr ? sampleRate : nil
    }
}
