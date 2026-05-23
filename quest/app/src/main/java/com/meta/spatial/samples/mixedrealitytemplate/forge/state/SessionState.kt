package com.meta.spatial.samples.mixedrealitytemplate.forge.state

import android.util.Log
import com.meta.spatial.samples.mixedrealitytemplate.forge.camera.PassthroughCapture
import com.meta.spatial.samples.mixedrealitytemplate.forge.net.LiveSocket
import com.meta.spatial.samples.mixedrealitytemplate.forge.net.OrchestratorSocket
import com.meta.spatial.samples.mixedrealitytemplate.forge.net.SnapshotUploader
import com.meta.spatial.samples.mixedrealitytemplate.forge.net.SocketState
import com.meta.spatial.samples.mixedrealitytemplate.forge.proto.ActionCard
import com.meta.spatial.samples.mixedrealitytemplate.forge.proto.AuthorKind
import com.meta.spatial.samples.mixedrealitytemplate.forge.proto.ChannelInfo
import com.meta.spatial.samples.mixedrealitytemplate.forge.proto.ForgeJson
import com.meta.spatial.samples.mixedrealitytemplate.forge.proto.ForgeMsg
import com.meta.spatial.samples.mixedrealitytemplate.forge.proto.Risk
import com.meta.spatial.samples.mixedrealitytemplate.forge.proto.Speaker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** One entry in the HUD tool-call ticker. */
data class ToolCallUi(val name: String, val summary: String, val inFlight: Boolean)

/** Latest spoken/heard line shown in the HUD. */
data class TranscriptUi(val text: String, val speaker: Speaker)

/** A pending operator-instruction confirmation (drives the ConfirmationPanel). */
data class ConfirmationUi(
    val callId: String,
    val summary: String,
    val risk: Risk,
    val invokerSmeId: String?,
    val card: ActionCard?,
)

/**
 * Single source of truth for the Forge MR session UI. Pure Kotlin (no Spatial
 * SDK), instantiated directly by the activity (`AppSystemActivity` is not a
 * `ComponentActivity`, so no ViewModel factory). Consumes [OrchestratorSocket]
 * events and projects them into StateFlows the Compose panels observe.
 *
 * Chat rendering follows the wire model: server-side composites (SmeResponse,
 * DissentReport, SafetyInterrupt) are *mirrored into channels as ChatMessages*
 * (spec 00 §5), so the chat panel only needs to render ChatMessages +
 * ChannelUpdate deltas. `application/json` bodies carry typed cards.
 */
class SessionState(
    val socket: OrchestratorSocket,
    private val scope: CoroutineScope,
    private val capture: PassthroughCapture? = null,
    private val uploader: SnapshotUploader? = null,
    private val liveSocket: LiveSocket? = null,
) {
    val connection: StateFlow<SocketState> = socket.state

    /** True once CAMERA + HEADSET_CAMERA + RECORD_AUDIO are all granted (in-VR). */
    private val _mediaReady = MutableStateFlow(false)
    val mediaReady: StateFlow<Boolean> = _mediaReady.asStateFlow()

    private val _snapshotInFlight = MutableStateFlow(false)
    val snapshotInFlight: StateFlow<Boolean> = _snapshotInFlight.asStateFlow()

    /** Gemini Live duplex session active (mic + JPEG out, TTS back). */
    val liveActive: StateFlow<Boolean> = liveSocket?.active ?: MutableStateFlow(false).asStateFlow()

    /** Set by the activity; invoked to request camera+mic perms in-VR on first use. */
    var onRequestCameraPermission: (() -> Unit)? = null

    private val _channels = MutableStateFlow<List<ChannelInfo>>(emptyList())
    val channels: StateFlow<List<ChannelInfo>> = _channels.asStateFlow()

    private val _messages = MutableStateFlow<Map<String, List<ForgeMsg.ChatMessage>>>(emptyMap())
    val messages: StateFlow<Map<String, List<ForgeMsg.ChatMessage>>> = _messages.asStateFlow()

    private val _selectedChannel = MutableStateFlow(DEFAULT_CHANNEL)
    val selectedChannel: StateFlow<String> = _selectedChannel.asStateFlow()

    private val _transcript = MutableStateFlow<TranscriptUi?>(null)
    val transcript: StateFlow<TranscriptUi?> = _transcript.asStateFlow()

    private val _toolCalls = MutableStateFlow<List<ToolCallUi>>(emptyList())
    val toolCalls: StateFlow<List<ToolCallUi>> = _toolCalls.asStateFlow()

    private val _confirmation = MutableStateFlow<ConfirmationUi?>(null)
    val confirmation: StateFlow<ConfirmationUi?> = _confirmation.asStateFlow()

    fun start() {
        socket.start()
        scope.launch {
            socket.events.collect { onEvent(it) }
        }
    }

    fun stop() {
        liveSocket?.stop()
        socket.stop()
    }

    fun selectChannel(id: String) {
        _selectedChannel.value = id
    }

    /** Optimistically render the user's line, then ship it; the server routes by @mention. */
    fun sendChat(body: String) {
        if (body.isBlank()) return
        val channel = _selectedChannel.value
        val mine =
            ForgeMsg.ChatMessage(
                channelId = channel,
                authorId = "@user",
                authorKind = AuthorKind.USER,
                body = body,
                messageId = OrchestratorSocket.newUlid(),
                ts = System.currentTimeMillis() * 1_000_000L,
            )
        upsertMessage(mine)
        socket.sendChat(body, channel)
    }

    fun respondConfirmation(approved: Boolean) {
        val c = _confirmation.value ?: return
        socket.respondConfirmation(c.callId, approved)
        _confirmation.value = null
    }

    fun setMediaReady(ready: Boolean) {
        _mediaReady.value = ready
    }

    /** Toggle the always-on Gemini Live session (voice + camera → agent orchestration). */
    fun toggleLive() {
        val live = liveSocket
        if (live == null) {
            systemLine("Live not available in this build.")
            return
        }
        if (!_mediaReady.value) {
            systemLine("Approve camera + microphone in the headset, then tap 🎙 again.")
            onRequestCameraPermission?.invoke()
            return
        }
        if (live.active.value) {
            live.stop()
            systemLine("Live session ended.")
        } else {
            live.start()
            systemLine("🎙 Live — talk to Forge; the guild is watching and listening.")
        }
    }

    /**
     * Capture a world-facing still and POST it to /v2/snapshot. The resulting
     * SnapshotAnalysis card returns asynchronously over the chat socket (routed
     * by the shared sessionId) and renders via the existing ChatPanel card path.
     */
    fun captureAndAnalyze(note: String? = null) {
        val cap = capture
        val up = uploader
        if (cap == null || up == null) {
            systemLine("Camera not available in this build.")
            return
        }
        if (!_mediaReady.value) {
            systemLine("Approve the camera prompt in the headset, then tap 📷 again.")
            onRequestCameraPermission?.invoke()
            return
        }
        if (_snapshotInFlight.value) return
        _snapshotInFlight.value = true
        scope.launch {
            try {
                val still = cap.captureStill()
                systemLine("📷 analyzing snapshot (${still.width}×${still.height})…")
                up.upload(socket.sessionId, still.jpeg, still.width, still.height, note)
                    .onFailure { systemLine("Snapshot upload failed: ${it.message}") }
            } catch (e: SecurityException) {
                _mediaReady.value = false
                systemLine("Camera permission needed — approve it in the headset.")
            } catch (e: Exception) {
                systemLine("Snapshot capture failed: ${e.message}")
            } finally {
                _snapshotInFlight.value = false
            }
        }
    }

    /** Insert a local system message into #live-feed (analysis lands there too). */
    private fun systemLine(text: String) {
        upsertMessage(
            ForgeMsg.ChatMessage(
                channelId = "#live-feed",
                authorId = "@forge",
                authorKind = AuthorKind.SYSTEM,
                body = text,
                messageId = OrchestratorSocket.newUlid(),
                ts = System.currentTimeMillis() * 1_000_000L,
            ),
        )
    }

    // ── event projection ────────────────────────────────────────────────────
    private fun onEvent(msg: ForgeMsg) {
        when (msg) {
            is ForgeMsg.ChannelList -> onChannelList(msg.channels)
            is ForgeMsg.ChatMessage -> upsertMessage(msg)
            is ForgeMsg.ChannelUpdate -> applyDelta(msg)
            is ForgeMsg.Transcript -> _transcript.value = TranscriptUi(msg.text, msg.speaker)
            is ForgeMsg.ToolCall ->
                _toolCalls.update {
                    (it + ToolCallUi(msg.name, summarizeArgs(msg.argsJson), inFlight = true))
                        .takeLast(MAX_TICKER)
                }
            is ForgeMsg.ToolResult ->
                _toolCalls.update { list ->
                    list.map { if (it.inFlight) it.copy(inFlight = false) else it }
                }
            is ForgeMsg.ConfirmationRequest -> _confirmation.value = toConfirmationUi(msg)
            else -> Log.d(TAG, "unhandled ${msg::class.simpleName}")
        }
    }

    private fun onChannelList(channels: List<ChannelInfo>) {
        _channels.value = channels
        // Seed empty buckets so the channel rail shows every channel immediately.
        _messages.update { existing ->
            val merged = existing.toMutableMap()
            channels.forEach { merged.putIfAbsent(it.id, emptyList()) }
            merged
        }
        if (_selectedChannel.value !in channels.map { it.id }) {
            _selectedChannel.value =
                channels.firstOrNull { it.id == DEFAULT_CHANNEL }?.id
                    ?: channels.firstOrNull()?.id
                    ?: DEFAULT_CHANNEL
        }
    }

    private fun upsertMessage(m: ForgeMsg.ChatMessage) {
        _messages.update { map ->
            val bucket = map[m.channelId].orEmpty()
            val idx = bucket.indexOfFirst { it.messageId == m.messageId }
            val next = if (idx >= 0) bucket.toMutableList().also { it[idx] = m } else bucket + m
            map + (m.channelId to next)
        }
    }

    private fun applyDelta(u: ForgeMsg.ChannelUpdate) {
        _messages.update { map ->
            var changed = false
            val next =
                map.mapValues { (_, bucket) ->
                    bucket.map { msg ->
                        if (msg.messageId == u.messageId) {
                            changed = true
                            msg.copy(body = msg.body + u.deltaText, streaming = !u.done)
                        } else msg
                    }
                }
            if (changed) next else map
        }
    }

    private fun toConfirmationUi(r: ForgeMsg.ConfirmationRequest): ConfirmationUi {
        val card =
            r.actionCardJson?.let {
                runCatching { ForgeJson.decodeFromString(ActionCard.serializer(), it) }
                    .onFailure { e -> Log.w(TAG, "bad actionCardJson: ${e.message}") }
                    .getOrNull()
            }
        return ConfirmationUi(r.callId, r.summary, r.risk, r.invokerSmeId, card)
    }

    private fun summarizeArgs(argsJson: String): String =
        argsJson.replace(Regex("\\s+"), " ").take(48)

    private fun <T> MutableStateFlow<T>.update(block: (T) -> T) {
        value = block(value)
    }

    companion object {
        private const val TAG = "ForgeSession"
        private const val DEFAULT_CHANNEL = "#live-feed"
        private const val MAX_TICKER = 6
    }
}
