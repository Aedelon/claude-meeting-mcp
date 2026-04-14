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
    private var callbackCount: Int = 0

    /// Ring buffer holds ~5 seconds of stereo float data
    private let ringCapacity: Int

    init(aggregateID: AudioObjectID, outputPath: String) throws {
        self.aggregateID = aggregateID
        self.outputURL = URL(fileURLWithPath: outputPath)
        self.ringCapacity = Int(sampleRate) * 2 * 5  // 5 sec of stereo floats
        self.ringBuffer = RingBuffer(capacity: ringCapacity)

        // Validate that the processing format is constructible
        guard AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: sampleRate,
            channels: 2,
            interleaved: false
        ) != nil else {
            throw AudioCapError.fileCreationFailed(outputPath)
        }

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

        // Give writer queue time to process
        writerQueue.sync {
            self.flushRingBuffer()
        }

        fputs("audiocap: \(callbackCount) IOProc callbacks processed\n", stderr)
        fputs("audiocap: Ring buffer remaining: \(ringBuffer.availableToRead) samples\n", stderr)

        // Close file
        audioFile = nil

        fputs("audiocap: Recording stopped\n", stderr)
    }

    // MARK: - IOProc Callback

    /// The IOProc callback. Called on a real-time audio thread.
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
        recorder.callbackCount += 1

        // Use UnsafeMutableAudioBufferListPointer for safe iteration
        let abl = UnsafeMutableAudioBufferListPointer(
            UnsafeMutablePointer(mutating: inInputData)
        )

        let numBuffers = abl.count

        // Debug: log first callback info
        if recorder.callbackCount == 1 {
            fputs("audiocap: IOProc first callback - \(numBuffers) buffers\n", stderr)
            for (i, buf) in abl.enumerated() {
                fputs("audiocap:   buffer[\(i)]: \(buf.mNumberChannels) ch, \(buf.mDataByteSize) bytes\n", stderr)
            }
        }

        // Extract system audio (first buffer) and mic (second buffer)
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

        // If we only have one buffer (system only), still write it with silent mic
        let frames: Int
        if let _ = systemPtr, micPtr != nil {
            frames = min(systemFrames, micFrames)
        } else if let _ = systemPtr {
            frames = systemFrames
        } else {
            return noErr  // No data at all
        }

        guard frames > 0 else { return noErr }

        // Interleave into stereo: L = system, R = mic
        // We write interleaved pairs [L, R, L, R, ...] into the ring buffer
        let stereoCount = frames * 2
        let stereo = UnsafeMutablePointer<Float>.allocate(capacity: stereoCount)
        defer { stereo.deallocate() }

        for i in 0..<frames {
            // Left channel: system audio
            if let sys = systemPtr {
                if systemChannels >= 2 {
                    // Stereo system audio: average L+R to mono
                    stereo[i * 2] = (sys[i * systemChannels] + sys[i * systemChannels + 1]) * 0.5
                } else {
                    stereo[i * 2] = sys[i]
                }
            } else {
                stereo[i * 2] = 0.0
            }

            // Right channel: microphone (or silence if no mic buffer)
            if let mic = micPtr {
                stereo[i * 2 + 1] = mic[i]
            } else {
                stereo[i * 2 + 1] = 0.0
            }
        }

        recorder.ringBuffer.write(stereo, count: stereoCount)

        return noErr
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

        // Must be even (stereo: L, R pairs)
        let toRead = available - (available % 2)
        guard toRead > 0 else { return }

        let frameCount = toRead / 2  // 2 floats per frame (L + R)

        // Read interleaved data from ring buffer
        let interleavedBuf = UnsafeMutablePointer<Float>.allocate(capacity: toRead)
        defer { interleavedBuf.deallocate() }
        ringBuffer.read(into: interleavedBuf, count: toRead)

        // Create non-interleaved AVAudioPCMBuffer (what AVAudioFile expects)
        guard let buffer = AVAudioPCMBuffer(
            pcmFormat: audioFile.processingFormat,
            frameCapacity: AVAudioFrameCount(frameCount)
        ) else { return }

        buffer.frameLength = AVAudioFrameCount(frameCount)

        // De-interleave: split [L,R,L,R,...] into separate channel buffers
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
}
