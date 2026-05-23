package com.meta.spatial.samples.mixedrealitytemplate.forge.net

import android.util.Log
import com.meta.spatial.samples.mixedrealitytemplate.forge.audio.MicCapture
import com.meta.spatial.samples.mixedrealitytemplate.forge.audio.SpeakerPlayer
import com.meta.spatial.samples.mixedrealitytemplate.forge.camera.PassthroughCapture
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString

/**
 * Duplex Gemini Live client over `/v2/live` (spec 00 §4.1, backend
 * `LiveDuplexBridge`). Each outbound binary frame is `[1-byte type][payload]`:
 *   0x01 = PCM audio (16 kHz mono LE), 0x02 = JPEG frame.
 * Inbound binary frames are Gemini Live TTS audio (24 kHz mono PCM, 1-byte
 * prefix) → played on the speaker. Final transcripts + tool-calls are routed
 * into the graph server-side, so agent replies arrive over the existing
 * `/v2/chat` socket and render in the unified feed.
 *
 * Must use the SAME [sessionId] as the chat socket so both attach to one
 * orchestrator session.
 */
class LiveSocket(
    private val liveUrl: String,
    private val sessionId: String,
    private val scope: CoroutineScope,
    private val mic: MicCapture,
    private val speaker: SpeakerPlayer,
    private val capture: PassthroughCapture,
    private val enableVideo: Boolean = true,
) {
    private val _active = MutableStateFlow(false)
    val active: StateFlow<Boolean> = _active.asStateFlow()

    // No pingInterval: the session streams mic/video constantly, so OkHttp's
    // ping-timeout (which closed the socket at ~20s) is both unnecessary and harmful.
    private val http = OkHttpClient.Builder().readTimeout(0, TimeUnit.MILLISECONDS).build()

    @Volatile private var ws: WebSocket? = null
    @Volatile private var ttsFrames = 0
    private val jobs = mutableListOf<Job>()

    fun start() {
        if (ws != null) return
        val url = "$liveUrl?sessionId=$sessionId&client=quest"
        ws = http.newWebSocket(Request.Builder().url(url).build(), Listener())
        Log.i(TAG, "live connecting: $url")
    }

    fun stop() {
        jobs.forEach { it.cancel() }
        jobs.clear()
        try { capture.stopStreaming() } catch (_: Throwable) {}
        mic.stop()
        speaker.stop()
        ws?.close(1000, "client stop")
        ws = null
        _active.value = false
    }

    private fun frame(type: Byte, payload: ByteArray): ByteString {
        val out = ByteArray(payload.size + 1)
        out[0] = type
        System.arraycopy(payload, 0, out, 1, payload.size)
        return out.toByteString()
    }

    private inner class Listener : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            _active.value = true
            speaker.start()
            mic.start()
            Log.i(TAG, "live open — streaming mic${if (enableVideo) " + video" else ""}")

            jobs +=
                scope.launch {
                    mic.chunks.collect { pcm -> webSocket.send(frame(TYPE_AUDIO, pcm)) }
                }
            if (enableVideo) {
                jobs +=
                    scope.launch {
                        try {
                            capture.startStreaming(fps = 2.0, maxLongEdge = 512) { jpeg ->
                                webSocket.send(frame(TYPE_VIDEO, jpeg))
                            }
                        } catch (e: Exception) {
                            // Video is best-effort; audio keeps the session useful.
                            Log.w(TAG, "video stream unavailable: ${e.message}")
                        }
                    }
            }
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            // Inbound TTS is BARE 24 kHz PCM — no type prefix (backend bridge.py
            // sends ev.audio verbatim). Play as-is.
            val arr = bytes.toByteArray()
            if (arr.isEmpty()) return
            if (ttsFrames == 0) Log.i(TAG, "first TTS frame (${arr.size}B) — duplex confirmed")
            ttsFrames++
            speaker.enqueue(arr)
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(1000, null)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            Log.i(TAG, "live closed ($code)")
            stop()
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            Log.w(TAG, "live failure: ${t.message}")
            stop()
        }
    }

    companion object {
        private const val TAG = "ForgeLive"
        private const val TYPE_AUDIO: Byte = 0x01
        private const val TYPE_VIDEO: Byte = 0x02
    }
}
