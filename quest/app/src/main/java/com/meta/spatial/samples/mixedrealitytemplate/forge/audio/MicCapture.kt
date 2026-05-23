package com.meta.spatial.samples.mixedrealitytemplate.forge.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow

/**
 * Microphone capture for Gemini Live: 16 kHz mono PCM16, emitted in ~100 ms
 * blocks (1600 samples = 3200 bytes) to match the /v2/live audio contract.
 * Each emitted [ByteArray] is the raw little-endian PCM payload (no prefix —
 * [com.meta.spatial.samples.mixedrealitytemplate.forge.net.LiveSocket] adds the
 * 0x01 type byte).
 */
class MicCapture {
    private val _chunks =
        MutableSharedFlow<ByteArray>(extraBufferCapacity = 32, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val chunks: SharedFlow<ByteArray> = _chunks.asSharedFlow()

    @Volatile private var record: AudioRecord? = null
    @Volatile private var thread: Thread? = null

    @SuppressLint("MissingPermission") // caller ensures RECORD_AUDIO is granted
    fun start() {
        if (record != null) return
        val minBuf =
            AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (minBuf <= 0) {
            Log.w(TAG, "getMinBufferSize failed: $minBuf")
            return
        }
        val ar =
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                maxOf(minBuf, BLOCK_BYTES * 4),
            )
        if (ar.state != AudioRecord.STATE_INITIALIZED) {
            Log.w(TAG, "AudioRecord not initialized")
            ar.release()
            return
        }
        record = ar
        ar.startRecording()
        thread =
            Thread {
                    val buf = ByteArray(BLOCK_BYTES)
                    while (record != null) {
                        val n = ar.read(buf, 0, buf.size)
                        if (n > 0) _chunks.tryEmit(buf.copyOf(n))
                    }
                }
                .apply { name = "ForgeMic"; start() }
        Log.i(TAG, "mic capture started ($SAMPLE_RATE Hz mono)")
    }

    fun stop() {
        val ar = record
        record = null
        thread = null
        try { ar?.stop() } catch (_: Throwable) {}
        try { ar?.release() } catch (_: Throwable) {}
    }

    companion object {
        private const val TAG = "ForgeMic"
        const val SAMPLE_RATE = 16_000
        private const val BLOCK_BYTES = 3_200 // 1600 samples * 2 bytes ≈ 100 ms
    }
}
