import Foundation

/// Lock-free single-producer single-consumer ring buffer for raw bytes.
/// The IOProc callback (real-time thread) writes, the file writer thread reads.
final class RingBuffer {
    private let capacity: Int
    private let buffer: UnsafeMutableRawPointer
    private var writeIndex: Int = 0
    private var readIndex: Int = 0

    init(capacity: Int) {
        self.capacity = capacity
        self.buffer = UnsafeMutableRawPointer.allocate(byteCount: capacity, alignment: 16)
        self.buffer.initializeMemory(as: UInt8.self, repeating: 0, count: capacity)
    }

    deinit {
        buffer.deallocate()
    }

    var availableBytesToRead: Int {
        let w = writeIndex
        let r = readIndex
        if w >= r { return w - r }
        return capacity - r + w
    }

    var availableBytesToWrite: Int {
        return capacity - 1 - availableBytesToRead
    }

    /// Write raw bytes. Returns number of bytes actually written.
    /// Safe to call from the real-time IOProc thread.
    @discardableResult
    func writeBytes(_ source: UnsafeRawPointer, count: Int) -> Int {
        let toWrite = min(count, availableBytesToWrite)
        if toWrite == 0 { return 0 }

        let w = writeIndex
        let firstChunk = min(toWrite, capacity - w)
        buffer.advanced(by: w).copyMemory(from: source, byteCount: firstChunk)

        if firstChunk < toWrite {
            let secondChunk = toWrite - firstChunk
            buffer.copyMemory(from: source.advanced(by: firstChunk), byteCount: secondChunk)
        }

        writeIndex = (w + toWrite) % capacity
        return toWrite
    }

    /// Read raw bytes into destination. Returns number of bytes actually read.
    @discardableResult
    func readBytes(_ destination: UnsafeMutableRawPointer, count: Int) -> Int {
        let toRead = min(count, availableBytesToRead)
        if toRead == 0 { return 0 }

        let r = readIndex
        let firstChunk = min(toRead, capacity - r)
        destination.copyMemory(from: buffer.advanced(by: r), byteCount: firstChunk)

        if firstChunk < toRead {
            let secondChunk = toRead - firstChunk
            destination.advanced(by: firstChunk).copyMemory(
                from: buffer, byteCount: secondChunk
            )
        }

        readIndex = (r + toRead) % capacity
        return toRead
    }
}
