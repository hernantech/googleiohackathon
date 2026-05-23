package com.meta.spatial.samples.mixedrealitytemplate.forge.audio

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.util.Log

/**
 * Streaming speaker for Gemini Live TTS output: 24 kHz mono PCM16 (Live's output
 * rate). [enqueue] writes raw PCM payload (prefix already stripped by LiveSocket).
 */
class SpeakerPlayer {
    private val lock = Any()
    private var track: AudioTrack? = null

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
                    minBuf * 2,
                    AudioTrack.MODE_STREAM,
                    AudioManager.AUDIO_SESSION_ID_GENERATE,
                )
            t.play()
            track = t
            Log.i(TAG, "speaker started ($SAMPLE_RATE Hz mono)")
        }
    }

    fun enqueue(pcm: ByteArray) {
        val t = synchronized(lock) { track } ?: return
        try {
            t.write(pcm, 0, pcm.size, AudioTrack.WRITE_BLOCKING)
        } catch (e: Throwable) {
            Log.w(TAG, "speaker write failed: ${e.message}")
        }
    }

    fun stop() {
        synchronized(lock) {
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
