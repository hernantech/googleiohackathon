import Foundation

// Tool-result payloads. These travel inside `AgentEvent.toolResult.resultJson`
// as a JSON string, not as top-level wire frames. Field names mirror
// `forge_orchestrator/proto/events.py` exactly (camelCase on the wire).

struct LookAtBenchResult: Codable, Equatable {
    let components: [DetectedComponent]
    let frameTimestampNs: Int64?
    let sceneSummary: String?

    init(components: [DetectedComponent], frameTimestampNs: Int64? = nil, sceneSummary: String? = nil) {
        self.components = components
        self.frameTimestampNs = frameTimestampNs
        self.sceneSummary = sceneSummary
    }
}

struct MeterReadResult: Codable, Equatable {
    let value: Double
    let unit: String
    let mode: String?
    let confidence: Float

    init(value: Double, unit: String, mode: String? = nil, confidence: Float = 1.0) {
        self.value = value
        self.unit = unit
        self.mode = mode
        self.confidence = confidence
    }

    private enum CodingKeys: String, CodingKey { case value, unit, mode, confidence }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        value = try c.decode(Double.self, forKey: .value)
        unit = try c.decode(String.self, forKey: .unit)
        mode = try c.decodeIfPresent(String.self, forKey: .mode)
        confidence = try c.decodeIfPresent(Float.self, forKey: .confidence) ?? 1.0
    }
}

struct ChipMarkingResult: Codable, Equatable {
    let partNumber: String
    let datasheetUri: String?
    let confidence: Float

    init(partNumber: String, datasheetUri: String? = nil, confidence: Float = 1.0) {
        self.partNumber = partNumber
        self.datasheetUri = datasheetUri
        self.confidence = confidence
    }

    private enum CodingKeys: String, CodingKey { case partNumber, datasheetUri, confidence }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        partNumber = try c.decode(String.self, forKey: .partNumber)
        datasheetUri = try c.decodeIfPresent(String.self, forKey: .datasheetUri)
        confidence = try c.decodeIfPresent(Float.self, forKey: .confidence) ?? 1.0
    }
}

struct CaptureLogicResult: Codable, Equatable {
    let captureId: String
    let sampleRateHz: Int
    let channels: [Int]
    let durationMs: Int
}

struct DecodedFrame: Codable, Equatable {
    let timestampNs: Int64
    let payloadHex: String
    let addressHex: String?
    let ack: Bool?
}

struct DecodeProtocolResult: Codable, Equatable {
    let `protocol`: String
    let frames: [DecodedFrame]
}

struct FixDiagramResult: Codable, Equatable {
    let imageUri: String
    let captionMarkdown: String?
}

struct PublishReportResult: Codable, Equatable {
    let docUri: String
}

extension LookAtBenchResult {
    /// Decode from the `resultJson` string carried by `AgentEvent.toolResult`.
    static func from(resultJson: String) -> LookAtBenchResult? {
        guard let data = resultJson.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(LookAtBenchResult.self, from: data)
    }
}
