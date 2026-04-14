import Foundation

/// Lock-free single-producer single-consumer ring buffer for Float samples.
/// The IOProc callback (real-time thread) writes, the file writer thread reads.
final class RingBuffer {
    private let capacity: Int
    private let buffer: UnsafeMutablePointer<Float>
    private var writeIndex: Int = 0
    private var readIndex: Int = 0

    /// Create a ring buffer with the given capacity in Float samples.
    init(capacity: Int) {
        self.capacity = capacity
        self.buffer = .allocate(capacity: capacity)
        self.buffer.initialize(repeating: 0.0, count: capacity)
    }

    deinit {
        buffer.deallocate()
    }

    /// Number of samples available to read.
    var availableToRead: Int {
        let w = writeIndex
        let r = readIndex
        if w >= r { return w - r }
        return capacity - r + w
    }

    /// Number of samples that can be written.
    var availableToWrite: Int {
        return capacity - 1 - availableToRead
    }

    /// Write samples into the ring buffer. Returns number of samples actually written.
    /// Safe to call from the real-time IOProc thread (no allocation, no locks).
    @discardableResult
    func write(_ source: UnsafePointer<Float>, count: Int) -> Int {
        let toWrite = min(count, availableToWrite)
        if toWrite == 0 { return 0 }

        let w = writeIndex
        let firstChunk = min(toWrite, capacity - w)
        buffer.advanced(by: w).update(from: source, count: firstChunk)

        if firstChunk < toWrite {
            let secondChunk = toWrite - firstChunk
            buffer.update(from: source.advanced(by: firstChunk), count: secondChunk)
        }

        writeIndex = (w + toWrite) % capacity
        return toWrite
    }

    /// Read samples from the ring buffer into the destination.
    /// Returns number of samples actually read.
    @discardableResult
    func read(into destination: UnsafeMutablePointer<Float>, count: Int) -> Int {
        let toRead = min(count, availableToRead)
        if toRead == 0 { return 0 }

        let r = readIndex
        let firstChunk = min(toRead, capacity - r)
        destination.update(from: buffer.advanced(by: r), count: firstChunk)

        if firstChunk < toRead {
            let secondChunk = toRead - firstChunk
            destination.advanced(by: firstChunk).update(from: buffer.advanced(by: r), count: secondChunk)
        }

        readIndex = (r + toRead) % capacity
        return toRead
    }
}
