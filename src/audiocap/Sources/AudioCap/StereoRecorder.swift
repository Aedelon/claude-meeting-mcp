import AVFoundation
import CoreAudio
import Foundation

/// Records stereo WAV from an aggregate device (tap + mic).
/// Left channel = system audio (tap), Right channel = microphone.
final class StereoRecorder {
    private let aggregateID: AudioObjectID
    private let outputURL: URL
    private let sampleRate: Double = 44100.0

    private var ioProcID: AudioDeviceIOProcID?
    private var audioFile: AVAudioFile?
    private let ringBuffer: RingBuffer
    private let writerQueue = DispatchQueue(label: "audiocap.writer")
    private var isRunning = false
    private var writerTimer: DispatchSourceTimer?

    /// Stereo interleaved frame: 2 floats per frame (L + R)
    private let framesPerCallback: Int = 512
    /// Ring buffer holds ~5 seconds of stereo float data
    private let ringCapacity: Int

    init(aggregateID: AudioObjectID, outputPath: String) throws {
        self.aggregateID = aggregateID
        self.outputURL = URL(fileURLWithPath: outputPath)
        self.ringCapacity = Int(sampleRate) * 2 * 5  // 5 sec of stereo floats
        self.ringBuffer = RingBuffer(capacity: ringCapacity)

        // Create output WAV file
        do {
            let settings: [String: Any] = [
                AVFormatIDKey: kAudioFormatLinearPCM,
                AVSampleRateKey: sampleRate,
                AVNumberOfChannelsKey: 2,
                AVLinearPCMBitDepthKey: 16,
                AVLinearPCMIsFloatKey: false,
                AVLinearPCMIsBigEndianKey: false,
                AVLinearPCMIsNonInterleaved: false,
            ]
            self.audioFile = try AVAudioFile(
                forWriting: outputURL,
                settings: settings,
                commonFormat: .pcmFormatFloat32,
                interleaved: true
            )
        } catch {
            throw AudioCapError.fileCreationFailed("\(outputPath): \(error)")
        }
    }

    func start() throws {
        // Create IOProc - pass self as client data
        let clientData = Unmanaged.passUnretained(self).toOpaque()
        var procID: AudioDeviceIOProcID?
        let status = AudioDeviceCreateIOProcID(
            aggregateID,
            ioProc,
            clientData,
            &procID
        )
        guard status == noErr, let procID = procID else {
            throw AudioCapError.ioProcFailed(status)
        }
        self.ioProcID = procID

        // Start the device
        let startStatus = AudioDeviceStart(aggregateID, procID)
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
        flushRingBuffer()

        // Close file
        audioFile = nil

        fputs("audiocap: Recording stopped\n", stderr)
    }

    // MARK: - IOProc Callback

    /// The IOProc callback. Called on a real-time audio thread.
    /// MUST NOT allocate memory, take locks, or do I/O.
    private let ioProc: AudioDeviceIOProc = {
        (
            _: AudioObjectID,
            _: UnsafePointer<AudioTimeStamp>,
            inInputData: UnsafePointer<AudioBufferList>,
            _: UnsafePointer<AudioTimeStamp>,
            _: UnsafeMutablePointer<AudioBufferList>,
            _: UnsafePointer<AudioTimeStamp>,
            inClientData: UnsafeMutableRawPointer?
        ) -> OSStatus in

        guard let clientData = inClientData else { return noErr }
        let recorder = Unmanaged<StereoRecorder>.fromOpaque(clientData).takeUnretainedValue()

        // The aggregate device provides input buffers:
        // - Buffer 0: tap audio (system) — may be stereo
        // - Buffer 1+: mic input — mono
        let numBuffers = Int(inInputData.pointee.mNumberBuffers)

        var systemData: UnsafeMutablePointer<Float>?
        var systemFrames: Int = 0
        var systemChannels: Int = 0
        var micData: UnsafeMutablePointer<Float>?
        var micFrames: Int = 0

        // Access AudioBufferList buffers via pointer arithmetic
        withUnsafePointer(to: &UnsafeMutablePointer(mutating: inInputData).pointee.mBuffers) { ptr in
            for index in 0..<numBuffers {
                let buf = ptr.advanced(by: index).pointee
                guard let data = buf.mData else { continue }
                let channels = Int(buf.mNumberChannels)
                let frameCount = Int(buf.mDataByteSize) / (MemoryLayout<Float>.size * max(channels, 1))

                if index == 0 {
                    systemData = data.assumingMemoryBound(to: Float.self)
                    systemFrames = frameCount
                    systemChannels = channels
                } else if micData == nil {
                    micData = data.assumingMemoryBound(to: Float.self)
                    micFrames = frameCount
                }
            }
        }

        // Interleave: L = system (mono mixdown if stereo), R = mic
        let frames = min(systemFrames, micFrames)
        if frames > 0 {
            // Temporary stack buffer for interleaved stereo
            // frames * 2 floats (L, R, L, R, ...)
            let stereoCount = frames * 2
            let stereo = UnsafeMutablePointer<Float>.allocate(capacity: stereoCount)
            defer { stereo.deallocate() }

            for i in 0..<frames {
                // Left channel: system audio (average if stereo)
                if let sys = systemData {
                    if systemChannels >= 2 {
                        stereo[i * 2] = (sys[i * systemChannels] + sys[i * systemChannels + 1]) * 0.5
                    } else {
                        stereo[i * 2] = sys[i]
                    }
                } else {
                    stereo[i * 2] = 0.0
                }

                // Right channel: microphone
                if let mic = micData {
                    stereo[i * 2 + 1] = mic[i]
                } else {
                    stereo[i * 2 + 1] = 0.0
                }
            }

            recorder.ringBuffer.write(stereo, count: stereoCount)
        }

        return noErr
    }

    // MARK: - File Writer

    private func startWriterTimer() {
        let timer = DispatchSource.makeTimerSource(queue: writerQueue)
        timer.schedule(deadline: .now(), repeating: .milliseconds(100))
        timer.setEventHandler { [weak self] in
            self?.flushRingBuffer()
        }
        timer.resume()
        self.writerTimer = timer
    }

    private func flushRingBuffer() {
        let available = ringBuffer.availableToRead
        guard available > 0, let audioFile = audioFile else { return }

        // Must be even (stereo interleaved: L, R pairs)
        let toRead = available - (available % 2)
        guard toRead > 0 else { return }

        let frameCount = toRead / 2  // 2 samples per frame (L + R)
        guard let buffer = AVAudioPCMBuffer(
            pcmFormat: audioFile.processingFormat,
            frameCapacity: AVAudioFrameCount(frameCount)
        ) else { return }

        buffer.frameLength = AVAudioFrameCount(frameCount)

        // Read interleaved data from ring buffer
        guard let floatData = buffer.floatChannelData else { return }

        // For interleaved format, channel data pointer points to interleaved buffer
        let tempBuffer = UnsafeMutablePointer<Float>.allocate(capacity: toRead)
        defer { tempBuffer.deallocate() }

        ringBuffer.read(into: tempBuffer, count: toRead)

        // Copy to AVAudioPCMBuffer
        // For interleaved stereo, floatChannelData[0] contains all interleaved samples
        floatData[0].update(from: tempBuffer, count: toRead)

        do {
            try audioFile.write(from: buffer)
        } catch {
            fputs("audiocap: Write error: \(error)\n", stderr)
        }
    }
}
