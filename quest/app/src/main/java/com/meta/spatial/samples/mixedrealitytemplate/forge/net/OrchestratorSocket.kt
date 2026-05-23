package com.meta.spatial.samples.mixedrealitytemplate.forge.net

import android.util.Log
import com.meta.spatial.samples.mixedrealitytemplate.forge.proto.ForgeJson
import com.meta.spatial.samples.mixedrealitytemplate.forge.proto.ForgeMsg
import java.security.SecureRandom
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

/** Connection lifecycle for the orchestrator chat socket. */
sealed class SocketState {
    data object Connecting : SocketState()

    /** Open and `Hello` sent. [sessionId] echoes the connect query. */
    data class Open(val sessionId: String) : SocketState()

    /** Transport down; auto-reconnect is scheduled in [retryInSec]. */
    data class Degraded(val reason: String, val retryInSec: Long) : SocketState()

    data object Closed : SocketState()
}

/**
 * OkHttp WebSocket client for the Forge v2 `/v2/chat` channel (spec 00/04).
 *
 * Structurally modeled on `hackathon/forge_quest`'s OrchestratorSocket but
 * rewritten for v2: JSON-only frames (no binary FRAM/AUDI), `kind`
 * discriminator, and subprotocol auth instead of a bearer header. Inbound
 * frames are decoded to [ForgeMsg] and emitted on [events]; unknown `kind`s are
 * dropped (forward-compat, WP-3). [Ping] is answered with [Pong] automatically.
 *
 * @param baseUrl e.g. `ws://host:8080/v2/chat` — query params are appended.
 * @param authToken offered as a `Sec-WebSocket-Protocol` value when non-null;
 *        the dev orchestrator (no `ALLOWED_DEV_TOKENS`) accepts with none.
 */
class OrchestratorSocket(
    private val baseUrl: String,
    private val clientName: String = "quest",
    private val authToken: String? = null,
    private val scope: CoroutineScope,
) {
    val sessionId: String = newUlid()

    private val _state = MutableStateFlow<SocketState>(SocketState.Closed)
    val state: StateFlow<SocketState> = _state.asStateFlow()

    private val _events =
        MutableSharedFlow<ForgeMsg>(
            extraBufferCapacity = 256,
            onBufferOverflow = kotlinx.coroutines.channels.BufferOverflow.DROP_OLDEST,
        )
    val events: SharedFlow<ForgeMsg> = _events.asSharedFlow()

    private val http: OkHttpClient =
        OkHttpClient.Builder()
            .pingInterval(20, TimeUnit.SECONDS) // TCP-level keepalive
            .readTimeout(0, TimeUnit.MILLISECONDS) // sockets stay open
            .build()

    @Volatile private var ws: WebSocket? = null
    @Volatile private var stopped = false
    private var loop: Job? = null

    fun start() {
        if (loop != null) return
        stopped = false
        loop = scope.launch { connectLoop() }
    }

    fun stop() {
        stopped = true
        ws?.close(1000, "client stop")
        ws = null
        loop?.cancel()
        loop = null
        _state.value = SocketState.Closed
    }

    /** Send any message; the polymorphic encoder writes the `kind` discriminator. */
    fun send(msg: ForgeMsg): Boolean {
        val sock = ws ?: return false
        return sock.send(ForgeJson.encodeToString(ForgeMsg.serializer(), msg))
    }

    /** User-authored chat message. The server reads only `body` to drive the graph. */
    fun sendChat(body: String, channelId: String = "#user"): Boolean =
        send(
            ForgeMsg.ChatMessage(
                channelId = channelId,
                authorId = "@user",
                authorKind = com.meta.spatial.samples.mixedrealitytemplate.forge.proto.AuthorKind.USER,
                body = body,
                messageId = newUlid(),
                ts = System.currentTimeMillis() * 1_000_000L,
            ),
        )

    fun respondConfirmation(callId: String, approved: Boolean): Boolean =
        send(ForgeMsg.ConfirmationResponse(callId = callId, approved = approved))

    private suspend fun connectLoop() {
        var attempt = 0
        while (scope.isActive && !stopped) {
            _state.value = SocketState.Connecting
            val closed = kotlinx.coroutines.CompletableDeferred<String>()
            openSocket(closed) { attempt = 0 } // reset backoff on first frame

            val reason = closed.await() // suspends until onClosed/onFailure
            ws = null
            if (stopped) break

            val backoff = backoffMs(attempt++)
            _state.value = SocketState.Degraded(reason, backoff / 1000)
            Log.w(TAG, "socket down ($reason); reconnecting in ${backoff}ms")
            delay(backoff)
        }
    }

    private fun openSocket(
        closed: kotlinx.coroutines.CompletableDeferred<String>,
        onFirstFrame: () -> Unit,
    ) {
        val url = "$baseUrl?sessionId=$sessionId&client=$clientName"
        val builder = Request.Builder().url(url)
        if (!authToken.isNullOrBlank()) {
            builder.addHeader("Sec-WebSocket-Protocol", authToken)
        }
        var gotFrame = false

        ws =
            http.newWebSocket(
                builder.build(),
                object : WebSocketListener() {
                    override fun onOpen(webSocket: WebSocket, response: Response) {
                        _state.value = SocketState.Open(sessionId)
                        webSocket.send(
                            ForgeJson.encodeToString(
                                ForgeMsg.serializer(),
                                ForgeMsg.Hello(client = clientName, sessionId = sessionId),
                            ),
                        )
                    }

                    override fun onMessage(webSocket: WebSocket, text: String) {
                        if (!gotFrame) {
                            gotFrame = true
                            onFirstFrame()
                        }
                        val msg =
                            try {
                                ForgeJson.decodeFromString(ForgeMsg.serializer(), text)
                            } catch (e: Exception) {
                                Log.d(TAG, "drop unparseable frame: ${e.message}")
                                return
                            }
                        if (msg is ForgeMsg.Ping) {
                            webSocket.send(
                                ForgeJson.encodeToString(
                                    ForgeMsg.serializer(),
                                    ForgeMsg.Pong(msg.nonce),
                                ),
                            )
                            return
                        }
                        _events.tryEmit(msg)
                    }

                    override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                        webSocket.close(1000, null)
                    }

                    override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                        if (!closed.isCompleted) closed.complete("closed($code)")
                    }

                    override fun onFailure(webSocket: WebSocket, t: Throwable, r: Response?) {
                        if (!closed.isCompleted) closed.complete(t.message ?: "failure")
                    }
                },
            )
    }

    private fun backoffMs(attempt: Int): Long =
        minOf(250L shl minOf(attempt, 6), 10_000L) // 250,500,1k,2k,4k,8k,16k→cap 10k

    companion object {
        private const val TAG = "ForgeSocket"
        private val CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ".toCharArray()
        private val rng = SecureRandom()

        /** Minimal ULID: 48-bit ms timestamp + 80 random bits, Crockford base32. */
        fun newUlid(): String {
            val sb = StringBuilder(26)
            var ts = System.currentTimeMillis()
            val time = CharArray(10)
            for (i in 9 downTo 0) {
                time[i] = CROCKFORD[(ts and 0x1f).toInt()]
                ts = ts shr 5
            }
            sb.append(time)
            for (i in 0 until 16) sb.append(CROCKFORD[rng.nextInt(32)])
            return sb.toString()
        }
    }
}
