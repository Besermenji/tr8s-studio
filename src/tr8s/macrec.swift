// tr8s-macrec: record a CoreAudio input device to a 32-bit float WAV.
//
//   tr8s-macrec DEVICE SECONDS RATE CHANNELS OUT.wav
//
// Why not ffmpeg: with Roland's TR-8S driver (1.0.3) on macOS 15, ffmpeg's AVFoundation input
// opens the device and delivers frames, but every sample is zero, the mix included. CoreAudio's
// AudioQueue (the route QuickTime takes) gets the real audio. All of the device's input channels
// are recorded and the first CHANNELS are kept, in device order (on the TR-8S, 1-2 are the mix).
// AudioQueue converts to RATE. audio.py compiles this file on first use.
import AudioToolbox
import CoreAudio
import Foundation

func fail(_ msg: String, _ code: Int32 = 1) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(code)
}

let a = CommandLine.arguments
guard a.count == 6, let seconds = Double(a[2]), let rate = Double(a[3]), let keep = Int(a[4]),
      seconds > 0, rate > 0, keep > 0 else { fail("usage: tr8s-macrec DEVICE SECONDS RATE CHANNELS OUT.wav", 2) }
let want = a[1], outPath = a[5]

func address(_ sel: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal)
    -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: sel, mScope: scope, mElement: kAudioObjectPropertyElementMain)
}

func string(_ id: AudioObjectID, _ sel: AudioObjectPropertySelector) -> String? {
    var addr = address(sel)
    var v: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &v) == noErr, let s = v else { return nil }
    return s.takeRetainedValue() as String
}

func inputChannels(_ id: AudioObjectID) -> Int {
    var addr = address(kAudioDevicePropertyStreamConfiguration, kAudioObjectPropertyScopeInput)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(id, &addr, 0, nil, &size) == noErr, size > 0 else { return 0 }
    let raw = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: 16)
    defer { raw.deallocate() }
    guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, raw) == noErr else { return 0 }
    let list = UnsafeMutableAudioBufferListPointer(raw.assumingMemoryBound(to: AudioBufferList.self))
    return list.reduce(0) { $0 + Int($1.mNumberChannels) }
}

// the input device with this name, and how many channels it has
var addr = address(kAudioHardwarePropertyDevices)
var size: UInt32 = 0
let sys = AudioObjectID(kAudioObjectSystemObject)
guard AudioObjectGetPropertyDataSize(sys, &addr, 0, nil, &size) == noErr else { fail("cannot list audio devices") }
var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
guard AudioObjectGetPropertyData(sys, &addr, 0, nil, &size, &ids) == noErr else { fail("cannot list audio devices") }
var found: (uid: String, channels: Int)?
var names: [String] = []
for id in ids {
    let n = inputChannels(id)
    guard n > 0, let name = string(id, kAudioObjectPropertyName) else { continue }
    names.append(name)
    if name == want, let uid = string(id, kAudioDevicePropertyDeviceUID) { found = (uid, n) }
}
guard let dev = found else { fail("no audio input named \(want) (inputs: \(names.joined(separator: ", ")))") }
guard keep <= dev.channels else { fail("\(want) has \(dev.channels) input channels, not \(keep)") }

let ch = dev.channels
var fmt = AudioStreamBasicDescription(
    mSampleRate: rate, mFormatID: kAudioFormatLinearPCM,
    mFormatFlags: kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked,
    mBytesPerPacket: UInt32(4 * ch), mFramesPerPacket: 1, mBytesPerFrame: UInt32(4 * ch),
    mChannelsPerFrame: UInt32(ch), mBitsPerChannel: 32, mReserved: 0)

// the WAV: an IEEE-float header whose sizes are filled in at the end
FileManager.default.createFile(atPath: outPath, contents: nil)
guard let out = FileHandle(forWritingAtPath: outPath) else { fail("cannot write \(outPath)") }
func le32(_ v: Int) -> Data { withUnsafeBytes(of: UInt32(v).littleEndian) { Data($0) } }
func le16(_ v: Int) -> Data { withUnsafeBytes(of: UInt16(v).littleEndian) { Data($0) } }
func header(_ dataBytes: Int) -> Data {
    var h = Data("RIFF".utf8) + le32(36 + dataBytes) + Data("WAVE".utf8)
    h += Data("fmt ".utf8) + le32(16) + le16(3) + le16(keep) + le32(Int(rate))
    h += le32(Int(rate) * 4 * keep) + le16(4 * keep) + le16(32)
    return h + Data("data".utf8) + le32(dataBytes)
}
out.write(header(0))

let total = Int(seconds * rate)
var frames = 0
let done = DispatchSemaphore(value: 0)
var queue: AudioQueueRef?
var st = AudioQueueNewInputWithDispatchQueue(&queue, &fmt, 0, DispatchQueue(label: "tr8s-macrec")) { aq, buf, _, _, _ in
    let n = min(Int(buf.pointee.mAudioDataByteSize) / (4 * ch), total - frames)
    if n > 0 {
        let src = buf.pointee.mAudioData.assumingMemoryBound(to: Float32.self)
        var chunk = Data(capacity: n * 4 * keep)
        if keep == ch {
            chunk.append(UnsafeBufferPointer(start: src, count: n * ch))
        } else {
            for f in 0..<n { chunk.append(UnsafeBufferPointer(start: src + f * ch, count: keep)) }
        }
        out.write(chunk)
        frames += n
    }
    if frames >= total { done.signal() } else { AudioQueueEnqueueBuffer(aq, buf, 0, nil) }
}
guard st == noErr, let aq = queue else { fail("AudioQueueNewInput failed: \(st)") }
var uid = dev.uid as CFString
st = AudioQueueSetProperty(aq, kAudioQueueProperty_CurrentDevice, &uid, UInt32(MemoryLayout<CFString>.size))
guard st == noErr else { fail("could not select \(want): \(st)") }
for _ in 0..<4 {
    var b: AudioQueueBufferRef?
    AudioQueueAllocateBuffer(aq, UInt32(4 * ch * 4096), &b)
    if let b = b { AudioQueueEnqueueBuffer(aq, b, 0, nil) }
}
guard AudioQueueStart(aq, nil) == noErr else { fail("could not start recording from \(want)") }
let late = done.wait(timeout: .now() + seconds + 10) == .timedOut
AudioQueueStop(aq, true)
AudioQueueDispose(aq, true)
out.seek(toFileOffset: 0)
out.write(header(frames * 4 * keep))
out.closeFile()
if late { fail("\(want) delivered \(frames) of \(total) frames before the deadline") }
