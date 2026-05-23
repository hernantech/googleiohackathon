package com.meta.spatial.samples.mixedrealitytemplate.forge.audio

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.util.Log
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * Streaming speaker for Gemini Live TTS (24 kHz mono PCM16). [enqueue] is
 * non-blocking — it hands the chunk to an internal queue drained by a dedicated
 * writer thread, so the WebSocket read thread never blocks on AudioTrack and
 * bursty delivery is absorbed by a ~400 ms jitter buffer (prevents the
 * "I only hear parts of it" underruns).
 */
class SpeakerPlayer {
    private val lock = Any()
    private var track: AudioTrack? = null
    private var writer: Thread? = null
    private val queue = LinkedBlockingQueue<ByteArray>()
    @Volatile private var running = false

    fun start() {
        synchronized(lock) {
            if (track != null) return
            val channel = AudioFormat.CHANNEL_OUT_MONO
            val encoding = AudioFormat.ENCODING_PCM_16BIT
            val minBuf = AudioTrack.getMinBufferSize(SAMPLE_RATE, channel, encoding)
            if (minBuf <= 0) {
                Log.w(TAG, "getMinBufferSize failed: $minBuf")
                return
            }
            // ~400 ms jitter buffer (and at least 4x the device minimum).
            val bufferSize = maxOf(minBuf * 4, SAMPLE_RATE * 2 * 400 / 1000)
            val t =
                AudioTrack(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build(),
                    AudioFormat.Builder()
                        .setEncoding(encoding)
                        .setSampleRate(SAMPLE_RATE)
                        .setChannelMask(channel)
                        .build(),
                    bufferSize,
                    AudioTrack.MODE_STREAM,
                    AudioManager.AUDIO_SESSION_ID_GENERATE,
                )
            t.play()
            track = t
            running = true
            queue.clear()
            writer =
                Thread {
                        while (running) {
                            val pcm =
                                try {
                                    queue.poll(100, TimeUnit.MILLISECONDS)
                                } catch (_: InterruptedException) {
                                    null
                                } ?: continue
                            try {
                                t.write(pcm, 0, pcm.size, AudioTrack.WRITE_BLOCKING)
                            } catch (e: Throwable) {
                                Log.w(TAG, "speaker write failed: ${e.message}")
                            }
                        }
                    }
                    .apply { name = "ForgeSpeaker"; start() }
            Log.i(TAG, "speaker started ($SAMPLE_RATE Hz mono, buf=$bufferSize)")
        }
    }

    /** Non-blocking: queue the chunk for the writer thread. */
    fun enqueue(pcm: ByteArray) {
        if (running) queue.offer(pcm)
    }

    fun stop() {
        synchronized(lock) {
            running = false
            writer?.interrupt()
            writer = null
            queue.clear()
            try { track?.stop() } catch (_: Throwable) {}
            try { track?.release() } catch (_: Throwable) {}
            track = null
        }
    }

    companion object {
        private const val TAG = "ForgeSpeaker"
        const val SAMPLE_RATE = 24_000
    }
}
