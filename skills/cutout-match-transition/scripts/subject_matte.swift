// Lift the subject (people, animals, objects) out of every frame of a video with Apple's Vision
// foreground-instance mask — the same model as "Lift subject" in Photos.
//
// subject_matte <video> <start seconds> <frame count>
// Writes raw RGBA frames (straight alpha = subject mask) to stdout; prints "W H fps" to stderr first.
import AVFoundation
import CoreImage
import Foundation
import Vision

let args = CommandLine.arguments
guard args.count >= 4, let start = Double(args[2]), let count = Int(args[3]) else {
    FileHandle.standardError.write("usage: subject_matte <video> <start s> <frames>\n".data(using: .utf8)!)
    exit(2)
}
let asset = AVURLAsset(url: URL(fileURLWithPath: args[1]))
let sema = DispatchSemaphore(value: 0)
var track: AVAssetTrack?
Task { track = try? await asset.loadTracks(withMediaType: .video).first; sema.signal() }
sema.wait()
guard let videoTrack = track else { FileHandle.standardError.write("no video track\n".data(using: .utf8)!); exit(1) }

let reader = try AVAssetReader(asset: asset)
let output = AVAssetReaderTrackOutput(track: videoTrack, outputSettings: [
    kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
])
output.alwaysCopiesSampleData = false
reader.add(output)
reader.timeRange = CMTimeRange(start: CMTime(seconds: start, preferredTimescale: 240000), duration: .positiveInfinity)
reader.startReading()

let stdout = FileHandle.standardOutput
var written = 0
var headerSent = false
while written < count, let sample = output.copyNextSampleBuffer(), let pb = CMSampleBufferGetImageBuffer(sample) {
    let w = CVPixelBufferGetWidth(pb), h = CVPixelBufferGetHeight(pb)
    if !headerSent {
        FileHandle.standardError.write("\(w) \(h)\n".data(using: .utf8)!)
        headerSent = true
    }
    // Subject mask (soft, 0...1) at full frame size; zeros if Vision finds no subject.
    var mask: CVPixelBuffer?
    let request = VNGenerateForegroundInstanceMaskRequest()
    let handler = VNImageRequestHandler(cvPixelBuffer: pb, options: [:])
    if (try? handler.perform([request])) != nil, let obs = request.results?.first {
        mask = try? obs.generateScaledMaskForImage(forInstances: obs.allInstances, from: handler)
    }
    var rgba = [UInt8](repeating: 0, count: w * h * 4)
    CVPixelBufferLockBaseAddress(pb, .readOnly)
    let src = CVPixelBufferGetBaseAddress(pb)!.assumingMemoryBound(to: UInt8.self)
    let srcStride = CVPixelBufferGetBytesPerRow(pb)
    var m: UnsafeMutablePointer<Float32>? = nil
    var mStride = 0
    if let mask {
        CVPixelBufferLockBaseAddress(mask, .readOnly)
        m = CVPixelBufferGetBaseAddress(mask)!.assumingMemoryBound(to: Float32.self)
        mStride = CVPixelBufferGetBytesPerRow(mask) / 4
    }
    for y in 0..<h {
        for x in 0..<w {
            let s = y * srcStride + x * 4, d = (y * w + x) * 4
            rgba[d] = src[s + 2]; rgba[d + 1] = src[s + 1]; rgba[d + 2] = src[s]  // BGRA -> RGB
            let a = m.map { max(0, min(1, $0[y * mStride + x])) } ?? 0
            rgba[d + 3] = UInt8(a * 255 + 0.5)
        }
    }
    if let mask { CVPixelBufferUnlockBaseAddress(mask, .readOnly) }
    CVPixelBufferUnlockBaseAddress(pb, .readOnly)
    stdout.write(Data(rgba))
    written += 1
}
FileHandle.standardError.write("frames \(written)\n".data(using: .utf8)!)
