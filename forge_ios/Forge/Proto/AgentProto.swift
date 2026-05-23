import Foundation
import simd

// MARK: - Wire envelope
//
// Mirrors `forge_orchestrator/forge_orchestrator/proto/events.py` and
// `forge_quest/.../proto/AgentProto.kt` 1:1. The JSON discriminator key is
// "kind" and its values are PascalCase ("Transcript", "ToolCall", …) — see
// AgentProtoCoding.swift. FROZEN: do not add or rename cases here; the decoder
// tolerates unknown kinds so a newer orchestrator stays compatible.

enum AgentEvent: Equatable {
    // v1 carryover, with v2-additive fields (specs/00 §2.1).
    case transcript(text: String, partial: Bool, ts: Int64, speaker: Speaker, smeId: String?)
    case toolCall(name: String, argsJson: String, callId: String)
    case toolResult(callId: String, resultJson: String, deferred: Bool)
    case confirmationRequest(callId: String, summary: String, risk: Risk, invokerSmeId: String?, actionCardJson: String?)
    case confirmationResponse(callId: String, approved: Bool, approverChannel: ApproverChannel)
    case audioChunk(pcmBase64: String, ts: Int64)
    case hello(client: String, sessionId: String, protocolVersion: String)
    case goodbye(reason: String)

    // v2 chat bus + control envelopes (specs/00 §2, specs/04).
    case chatMessage(ChatMessage)
    case channelUpdate(ChannelUpdate)
    case channelList(ChannelList)
    case channelHint(ChannelHint)
    case summonGuild(SummonGuild)
    case smeResponse(SmeResponse)
    case dissentReport(DissentReport)
    case safetyInterrupt(SafetyInterrupt)
    case checkpointMarker(CheckpointMarker)
    case replayDone(ReplayDone)
    case backpressureNotice(BackpressureNotice)
    case errorEvent(ErrorEvent)
    case ping(nonce: String)
    case pong(nonce: String)
    case subscribe(channelId: String)
    case unsubscribe(channelId: String)
}

enum Risk: String, Codable, Equatable {
    case low = "LOW"
    case medium = "MEDIUM"
    case high = "HIGH"
}

// MARK: - Camera & audio chunks (binary, sent on a separate WS frame)

struct FrameChunk: Equatable {
    let jpegBytes: Data
    let widthPx: Int
    let heightPx: Int
    let timestampNs: Int64
}

struct AudioInChunk: Equatable {
    let pcm: Data            // 16 kHz mono signed 16-bit little-endian
    let timestampNs: Int64
}

// MARK: - Intrinsics (per-session)

struct CameraIntrinsics: Codable, Equatable {
    let focalLengthPx: SIMD2<Float>     // (fx, fy)
    let principalPointPx: SIMD2<Float>  // (cx, cy)
    let distortionCoeffs: [Float]       // radial k1..k4, or empty if rectified
    let imageSizePx: SIMD2<Int32>       // (width, height)
}

// MARK: - Detections (returned by the orchestrator's look_at_bench tool)

struct Bbox2D: Codable, Equatable, Hashable {
    let x1: Int32
    let y1: Int32
    let x2: Int32
    let y2: Int32

    var centerPx: SIMD2<Float> { SIMD2(Float(x1 + x2) / 2, Float(y1 + y2) / 2) }
    var widthPx: Int32 { x2 - x1 }
    var heightPx: Int32 { y2 - y1 }
}

struct DetectedComponent: Codable, Equatable, Hashable {
    let id: String              // stable per-component identifier (e.g. "U1")
    let partNumber: String      // human-readable label (e.g. "STM32F411")
    let bbox: Bbox2D
    let confidence: Float
    let secondary: String?

    // Phase 2 — present when the orchestrator emits segmentation polygons:
    let maskPolygon: [SIMD2<Float>]?   // image-space contour, normalized 0..1; nil for bbox-only

    init(
        id: String,
        partNumber: String,
        bbox: Bbox2D,
        confidence: Float = 1.0,
        secondary: String? = nil,
        maskPolygon: [SIMD2<Float>]? = nil
    ) {
        self.id = id
        self.partNumber = partNumber
        self.bbox = bbox
        self.confidence = confidence
        self.secondary = secondary
        self.maskPolygon = maskPolygon
    }

    // The orchestrator omits `confidence` (defaults to 1.0) and `maskPolygon`
    // on bbox-only detections; decode tolerantly so those payloads parse.
    private enum CodingKeys: String, CodingKey {
        case id, partNumber, bbox, confidence, secondary, maskPolygon
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        partNumber = try c.decode(String.self, forKey: .partNumber)
        bbox = try c.decode(Bbox2D.self, forKey: .bbox)
        confidence = try c.decodeIfPresent(Float.self, forKey: .confidence) ?? 1.0
        secondary = try c.decodeIfPresent(String.self, forKey: .secondary)
        maskPolygon = try c.decodeIfPresent([SIMD2<Float>].self, forKey: .maskPolygon)
    }
}

// MARK: - Pose

struct Pose6dof: Equatable {
    let positionM: SIMD3<Float>
    let orientationQuat: simd_quatf     // (x, y, z, w)
    let timestampNs: Int64
}
