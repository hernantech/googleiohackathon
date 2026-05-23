import CoreImage
import Foundation

enum FrameEncoder {
    // Shared CIContext reused across calls; GPU-backed when available.
    private static let ciContext = CIContext(options: [.useSoftwareRenderer: false])

    static func encodeJPEG(_ buf: CVPixelBuffer, quality: CGFloat) -> Data {
        let image = CIImage(cvPixelBuffer: buf)
        let options: [CIImageRepresentationOption: Any] = [
            kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: quality
        ]
        guard let data = ciContext.jpegRepresentation(of: image,
                                                      colorSpace: CGColorSpaceCreateDeviceRGB(),
                                                      options: options) else {
            // Fallback: return empty data rather than crash; caller checks FrameChunk existence.
            return Data()
        }
        return data
    }
}
