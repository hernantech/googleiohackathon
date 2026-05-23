package com.meta.spatial.samples.mixedrealitytemplate.forge.proto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * Forge v2 wire protocol — Kotlin client side.
 *
 * Source of truth: `specs/00_wire_protocol.md §2.2` (the `AgentEvent` sealed
 * union) plus `orchestrator/chat_bus/envelopes.py` (the chat-bus-only control
 * messages that are NOT part of the union but ride the same socket). Every type
 * here is validated against the golden corpus under `testdata/wire/`.
 *
 * On the wire the discriminator field is `kind` (PascalCase, == class simple
 * name); all other fields are camelCase. We model every inbound/outbound message
 * as one sealed hierarchy [ForgeMsg] so a single polymorphic decode dispatches
 * on `kind`. Unknown `kind`s throw — the socket layer catches and drops them
 * (forward-compat, WP-3).
 */
@Serializable
sealed class ForgeMsg {

    // ── v1 carryover (unchanged in v2) ──────────────────────────────────────
    @Serializable
    @SerialName("Hello")
    data class Hello(
        val client: String,
        val sessionId: String,
        val protocolVersion: String = "2.0",
    ) : ForgeMsg()

    @Serializable
    @SerialName("Goodbye")
    data class Goodbye(val reason: String) : ForgeMsg()

    @Serializable
    @SerialName("Transcript")
    data class Transcript(
        val text: String,
        val partial: Boolean,
        val ts: Long,
        val speaker: Speaker = Speaker.USER,
        val smeId: String? = null,
    ) : ForgeMsg()

    @Serializable
    @SerialName("ToolCall")
    data class ToolCall(
        val name: String,
        val argsJson: String,
        val callId: String,
    ) : ForgeMsg()

    @Serializable
    @SerialName("ToolResult")
    data class ToolResult(
        val callId: String,
        val resultJson: String,
        val deferred: Boolean = false,
    ) : ForgeMsg()

    @Serializable
    @SerialName("ConfirmationRequest")
    data class ConfirmationRequest(
        val callId: String,
        val summary: String,
        val risk: Risk,
        val invokerSmeId: String? = null,
        val actionCardJson: String? = null,
    ) : ForgeMsg()

    @Serializable
    @SerialName("ConfirmationResponse")
    data class ConfirmationResponse(
        val callId: String,
        val approved: Boolean,
        val approverChannel: ApproverChannel = ApproverChannel.VOICE,
    ) : ForgeMsg()

    @Serializable
    @SerialName("AudioChunk")
    data class AudioChunk(val pcmBase64: String, val ts: Long) : ForgeMsg()

    // ── v2 additions ────────────────────────────────────────────────────────
    @Serializable
    @SerialName("ChatMessage")
    data class ChatMessage(
        val channelId: String,
        val authorId: String,
        val authorKind: AuthorKind,
        val body: String,
        val bodyContentType: BodyContentType = BodyContentType.MARKDOWN,
        val mentions: List<String> = emptyList(),
        val replyToId: String? = null,
        val messageId: String,
        val ts: Long,
        val streaming: Boolean = false,
    ) : ForgeMsg()

    @Serializable
    @SerialName("SummonGuild")
    data class SummonGuild(
        val callId: String,
        val topic: String,
        val smes: List<String>,
        val contextRefs: List<String> = emptyList(),
        val deadlineMs: Int = 30_000,
    ) : ForgeMsg()

    @Serializable
    @SerialName("SmeResponse")
    data class SmeResponse(
        val smeId: String,
        val callId: String,
        val confidence: Float,
        val claim: String,
        val rationale: String,
        val evidence: List<EvidenceRef> = emptyList(),
        val proposedActions: List<ProposedAction> = emptyList(),
        val dissentsWith: List<String> = emptyList(),
        val ts: Long,
    ) : ForgeMsg()

    @Serializable
    @SerialName("DissentReport")
    data class DissentReport(
        val callId: String,
        val parties: List<String>,
        val axis: String,
        val summary: String,
        val pairwise: List<DissentPair>,
        val ts: Long,
    ) : ForgeMsg()

    @Serializable
    @SerialName("ChannelUpdate")
    data class ChannelUpdate(
        val messageId: String,
        val deltaText: String,
        val done: Boolean = false,
        val ts: Long,
    ) : ForgeMsg()

    @Serializable
    @SerialName("SafetyInterrupt")
    data class SafetyInterrupt(
        val severity: Severity,
        val reason: String,
        val suggestedRecoverActions: List<ProposedAction> = emptyList(),
        val ts: Long,
    ) : ForgeMsg()

    @Serializable
    @SerialName("CheckpointMarker")
    data class CheckpointMarker(
        val checkpointId: String,
        val graphNodeName: String,
        val ts: Long,
    ) : ForgeMsg()

    // ── chat-bus-only control envelopes (orchestrator/chat_bus/envelopes.py) ──
    @Serializable
    @SerialName("ChannelList")
    data class ChannelList(val channels: List<ChannelInfo>) : ForgeMsg()

    @Serializable
    @SerialName("Ping")
    data class Ping(val nonce: String) : ForgeMsg()

    @Serializable
    @SerialName("Pong")
    data class Pong(val nonce: String) : ForgeMsg()

    @Serializable
    @SerialName("BackpressureNotice")
    data class BackpressureNotice(val dropped: Int, val sinceTs: Long) : ForgeMsg()

    @Serializable
    @SerialName("ReplayDone")
    data class ReplayDone(val resumeTs: Long, val checkpointId: String? = null) : ForgeMsg()

    @Serializable
    @SerialName("Subscribe")
    data class Subscribe(val channelId: String) : ForgeMsg()

    @Serializable
    @SerialName("Unsubscribe")
    data class Unsubscribe(val channelId: String) : ForgeMsg()

    @Serializable
    @SerialName("ErrorEvent")
    data class ErrorEvent(
        val code: String,
        val message: String,
        val causedByMessageId: String? = null,
        val ts: Long,
    ) : ForgeMsg()

    @Serializable
    @SerialName("ChannelHint")
    data class ChannelHint(
        val channelId: String,
        val hint: String,
        val reason: String,
    ) : ForgeMsg()
}

// ── Enums (lowercase / mime values carry @SerialName to match the wire) ──────
@Serializable
enum class Risk { LOW, MEDIUM, HIGH }

@Serializable
enum class Severity { WARN, HALT }

@Serializable
enum class Speaker {
    @SerialName("user") USER,
    @SerialName("live") LIVE,
    @SerialName("sme") SME,
}

@Serializable
enum class AuthorKind {
    @SerialName("user") USER,
    @SerialName("live") LIVE,
    @SerialName("sme") SME,
    @SerialName("system") SYSTEM,
}

@Serializable
enum class BodyContentType {
    @SerialName("text/markdown") MARKDOWN,
    @SerialName("application/json") JSON,
    @SerialName("text/code") CODE,
}

@Serializable
enum class ApproverChannel {
    @SerialName("voice") VOICE,
    @SerialName("chat") CHAT,
}

@Serializable
enum class Actor {
    @SerialName("operator") OPERATOR,
    @SerialName("guild") GUILD,
}

// ── Supporting types carried inside events (parity with Pydantic §2.1) ───────
@Serializable
data class EvidenceRef(val kind: String, val uri: String, val note: String? = null)

@Serializable
data class ProposedAction(
    val actor: Actor = Actor.OPERATOR,
    val tool: String,
    val argsJson: String,
    val rationale: String,
    val risk: Risk,
    val instruction: String? = null,
    val documentedLimitRef: String? = null,
)

@Serializable
data class DissentPair(
    val a: String,
    val b: String,
    val aClaim: String,
    val bClaim: String,
    val crux: String,
)

@Serializable
data class ChannelInfo(
    val id: String,
    val title: String,
    val smeId: String? = null,
    val icon: String? = null,
    val alwaysVisible: Boolean = false,
    val unreadHint: Int = 0,
)

/**
 * Rendered inside [ForgeMsg.ConfirmationRequest.actionCardJson] — the operator
 * instruction card. Decoded from the nested JSON string with [ForgeJson].
 */
@Serializable
data class ActionCard(
    val title: String,
    val bodyMarkdown: String,
    val diffMarkdown: String? = null,
    val risk: Risk,
    val documentedLimit: String? = null,
    val affirmLabel: String = "I did it",
    val denyLabel: String = "Skip",
)

/** On-demand snapshot result, carried inside a [ForgeMsg.ChatMessage] JSON body. */
@Serializable
data class FrameRef(
    val uri: String,
    val width: Int,
    val height: Int,
    val ts: Long,
    val sourceSeq: Int,
)

@Serializable
data class SnapshotAnalysis(
    val jobId: String,
    val frame: FrameRef,
    val model: String,
    val analysis: String,
    val cites: List<EvidenceRef> = emptyList(),
    val ts: Long,
)

/**
 * The single JSON configured for the Forge wire: `kind` discriminator,
 * tolerant of unknown fields (WP-3) and unknown enum-adjacent extras, and
 * emits defaults so outbound messages carry every required field.
 */
val ForgeJson: Json = Json {
    classDiscriminator = "kind"
    ignoreUnknownKeys = true
    encodeDefaults = true
    explicitNulls = false
}
