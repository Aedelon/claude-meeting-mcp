import AudioToolbox
import AVFoundation
import CoreAudio
import Foundation

/// Records audio from an aggregate device (system tap).
/// Based on the working pattern from obsfx/audiograb AudioCaptureSession.
final class StereoRecorder {
    private let deviceID: AudioObjectID
    private let outputPath: String
    private let ringBuffer: RingBuffer
    private var ioProcID: AudioDeviceIOProcID?
    private let writerQueue = DispatchQueue(label: "audiocap.writer", qos: .userInitiated)
    private var writerTimer: DispatchSourceTimer?
    private let sampleRate: Double
    private let sourceChannels: Int
    private var callbackCount: Int = 0

    // WAV writer
    private var audioFile: AVAudioFile?

    init(aggregateID: AudioObjectID, outputPath: String, tapFormat: AudioStreamBasicDescription) throws {
        self.deviceID = aggregateID
        self.outputPath = outputPath
        self.sampleRate = tapFormat.mSampleRate
        self.sourceChannels = Int(tapFormat.mChannelsPerFrame)

        // Ring buffer: ~5 seconds of raw float data
        let ringCapacity = Int(sampleRate) * sourceChannels * 5 * MemoryLayout<Float>.size
        self.ringBuffer = RingBuffer(capacity: ringCapacity)

        fputs("audiocap: Recorder init: \(sampleRate) Hz, \(sourceChannels) source ch\n", stderr)

        // Create WAV file: stereo 16-bit at tap's native sample rate
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: sampleRate,
            AVNumberOfChannelsKey: 2,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
            AVLinearPCMIsNonInterleaved: false,
        ]

        let outputURL = URL(fileURLWithPath: outputPath)
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
        // Pass ring buffer as client data (like audiograb does)
        let clientData = Unmanaged.passUnretained(self.ringBuffer).toOpaque()

        var procID: AudioDeviceIOProcID?
        let status = AudioDeviceCreateIOProcID(
            deviceID,
            { (_, _, inInputData, _, _, _, inClientData) -> OSStatus in
                guard let clientData = inClientData else { return noErr }
                let ringBuffer = Unmanaged<RingBuffer>.fromOpaque(clientData).takeUnretainedValue()

                let buf = inInputData.pointee.mBuffers
                guard let data = buf.mData, buf.mDataByteSize > 0 else { return noErr }
                _ = ringBuffer.writeBytes(data, count: Int(buf.mDataByteSize))

                return noErr
            },
            clientData,
            &procID
        )

        guard status == noErr else {
            fputs("audiocap: IOProc creation failed: \(status)\n", stderr)
            throw AudioCapError.ioProcFailed(status)
        }
        self.ioProcID = procID
        fputs("audiocap: IOProc created on device \(deviceID)\n", stderr)

        let startStatus = AudioDeviceStart(deviceID, procID)
        fputs("audiocap: AudioDeviceStart returned: \(startStatus)\n", stderr)
        guard startStatus == noErr else {
            if let p = procID {
                AudioDeviceDestroyIOProcID(deviceID, p)
            }
            ioProcID = nil
            throw AudioCapError.deviceStartFailed(startStatus)
        }

        startWriterTimer()
        fputs("audiocap: Recording started\n", stderr)
    }

    func stop() {
        writerTimer?.cancel()
        writerTimer = nil

        if let procID = ioProcID {
            AudioDeviceStop(deviceID, procID)
            AudioDeviceDestroyIOProcID(deviceID, procID)
            ioProcID = nil
        }

        // Drain remaining data
        drainRingBuffer()

        fputs("audiocap: Ring buffer remaining after drain: \(ringBuffer.availableBytesToRead) bytes\n", stderr)

        audioFile = nil
        fputs("audiocap: Recording stopped\n", stderr)
    }

    // MARK: - Writer

    private func startWriterTimer() {
        let timer = DispatchSource.makeTimerSource(queue: writerQueue)
        timer.schedule(deadline: .now(), repeating: .milliseconds(10))
        timer.setEventHandler { [weak self] in
            self?.drainRingBuffer()
        }
        timer.resume()
        writerTimer = timer
    }

    private func drainRingBuffer() {
        let available = ringBuffer.availableBytesToRead
        guard available > 0, let audioFile = audioFile else { return }

        let float32Size = MemoryLayout<Float32>.stride
        let bytesPerFrame = sourceChannels * float32Size
        let maxFrames = 4096
        let maxReadBytes = maxFrames * bytesPerFrame

        let readBuffer = UnsafeMutableRawPointer.allocate(byteCount: maxReadBytes, alignment: 16)
        defer { readBuffer.deallocate() }

        var remaining = available
        while remaining > 0 {
            let toRead = min(remaining, maxReadBytes)
            let alignedToRead = (toRead / bytesPerFrame) * bytesPerFrame
            guard alignedToRead > 0 else { break }

            let bytesRead = ringBuffer.readBytes(readBuffer, count: alignedToRead)
            guard bytesRead > 0 else { break }

            let frameCount = bytesRead / bytesPerFrame
            let srcPtr = readBuffer.assumingMemoryBound(to: Float32.self)

            // Create non-interleaved stereo buffer for AVAudioFile
            guard let buffer = AVAudioPCMBuffer(
                pcmFormat: audioFile.processingFormat,
                frameCapacity: AVAudioFrameCount(frameCount)
            ) else { break }

            buffer.frameLength = AVAudioFrameCount(frameCount)
            guard let channelData = buffer.floatChannelData else { break }

            let leftChannel = channelData[0]
            let rightChannel = channelData[1]

            if sourceChannels >= 2 {
                // Source is stereo: L = system L, R = system R
                for f in 0..<frameCount {
                    leftChannel[f] = srcPtr[f * sourceChannels]
                    rightChannel[f] = srcPtr[f * sourceChannels + 1]
                }
            } else {
                // Source is mono: duplicate to both channels
                for f in 0..<frameCount {
                    leftChannel[f] = srcPtr[f]
                    rightChannel[f] = srcPtr[f]
                }
            }

            do {
                try audioFile.write(from: buffer)
            } catch {
                fputs("audiocap: Write error: \(error)\n", stderr)
                break
            }

            remaining -= bytesRead
        }
    }
}
