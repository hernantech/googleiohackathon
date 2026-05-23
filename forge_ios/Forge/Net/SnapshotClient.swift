import Foundation

// MARK: - SnapshotClient
//
// Implements `POST /v2/snapshot?sessionId=&note=` (specs/00 §4.2, HANDOFF §3).
//
// The client captures one full-resolution still from AVCapturePhotoOutput
// (see CaptureSession.swift), downscales to SNAPSHOT_MAX_EDGE_PX if needed,
// and POSTs the JPEG bytes.  The server responds 202 with a jobId; the actual
// SnapshotAnalysis result arrives asynchronously over the chat bus as a
// ChatMessage(bodyContentType: .json, body: <SnapshotAnalysis JSON>).
//
// This is a one-shot HTTP request — no persistent socket.

struct SnapshotResponse: Codable {
    let jobId: String
}

enum SnapshotClientError: Error {
    case noJPEGData
    case httpError(Int)
    case decodeFailed
}

struct SnapshotClient {

    /// Maximum long-edge pixels before upload (specs/00 §4.2: "≤ SNAPSHOT_MAX_EDGE_PX").
    static let maxEdgePx: Int = 4096

    private let baseURL: URL
    private let authToken: String
    private let sessionId: String

    init(baseURL: URL, authToken: String, sessionId: String) {
        self.baseURL = baseURL
        self.authToken = authToken
        self.sessionId = sessionId
    }

    /// POST the JPEG bytes to /v2/snapshot.  Returns the server-assigned jobId.
    /// The SnapshotAnalysis arrives later over OrchestratorSocket events.
    func post(jpeg: Data, note: String? = nil) async throws -> String {
        guard !jpeg.isEmpty else { throw SnapshotClientError.noJPEGData }

        let url = snapshotURL(note: note)
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
        // Auth: shared secret via Authorization header (specs/00 §8 option b).
        request.setValue("Bearer \(authToken)", forHTTPHeaderField: "Authorization")

        let (data, response) = try await URLSession.shared.upload(for: request, from: jpeg)
        if let http = response as? HTTPURLResponse, http.statusCode != 202 {
            throw SnapshotClientError.httpError(http.statusCode)
        }
        guard let decoded = try? JSONDecoder().decode(SnapshotResponse.self, from: data) else {
            throw SnapshotClientError.decodeFailed
        }
        return decoded.jobId
    }

    // MARK: - Private

    private func snapshotURL(note: String?) -> URL {
        guard var comps = URLComponents(url: baseURL.appendingPathComponent("v2/snapshot"),
                                        resolvingAgainstBaseURL: false) else {
            return baseURL.appendingPathComponent("v2/snapshot")
        }
        var items = [URLQueryItem(name: "sessionId", value: sessionId)]
        if let note, !note.isEmpty {
            items.append(URLQueryItem(name: "note", value: note))
        }
        comps.queryItems = items
        return comps.url ?? baseURL.appendingPathComponent("v2/snapshot")
    }
}

// MARK: - JPEG downscale helper (specs/00 §4.2: client may downscale to ≤ 4096 px long edge)

import CoreGraphics
import ImageIO
import CoreImage
import UniformTypeIdentifiers

extension SnapshotClient {

    /// Downscale `jpeg` so the long edge is ≤ `maxEdgePx` (4096 px).
    /// Returns the original bytes if no downscale is needed.
    static func downscaleIfNeeded(_ jpeg: Data, maxEdge: Int = maxEdgePx) -> Data {
        guard let src = CGImageSourceCreateWithData(jpeg as CFData, nil),
              let props = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as? [CFString: Any],
              let w = props[kCGImagePropertyPixelWidth] as? Int,
              let h = props[kCGImagePropertyPixelHeight] as? Int else {
            return jpeg
        }
        let longEdge = max(w, h)
        guard longEdge > maxEdge else { return jpeg }

        let scale = Double(maxEdge) / Double(longEdge)
        let opts: [CFString: Any] = [
            kCGImageSourceThumbnailMaxPixelSize: maxEdge,
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
        ]
        guard let thumb = CGImageSourceCreateThumbnailAtIndex(src, 0, opts as CFDictionary) else {
            return jpeg
        }
        let mutable = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(mutable, UTType.jpeg.identifier as CFString, 1, nil) else {
            return jpeg
        }
        let quality: Double = scale < 0.5 ? 0.85 : 0.9
        CGImageDestinationAddImage(dest, thumb, [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { return jpeg }
        return mutable as Data
    }
}
