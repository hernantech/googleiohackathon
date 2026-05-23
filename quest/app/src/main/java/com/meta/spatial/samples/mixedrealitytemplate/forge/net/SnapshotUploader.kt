package com.meta.spatial.samples.mixedrealitytemplate.forge.net

import android.util.Log
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/**
 * Uploads a hi-res still to the orchestrator's `POST /v2/snapshot` (spec 00 §4.2).
 * The body is the RAW JPEG — the server wraps it in its FRAM container itself, so
 * we must NOT prepend any header. `sessionId` MUST match the open `/v2/chat`
 * socket so the resulting `SnapshotAnalysis` card routes back to our session.
 *
 * @param snapshotUrl e.g. `http://host:8080/v2/snapshot`
 */
class SnapshotUploader(private val snapshotUrl: String) {
    private val client =
        OkHttpClient.Builder()
            .callTimeout(15, TimeUnit.SECONDS)
            .build()

    /** POST the JPEG; returns the server's jobId on success. */
    suspend fun upload(
        sessionId: String,
        jpeg: ByteArray,
        width: Int,
        height: Int,
        note: String?,
    ): Result<String> =
        withContext(Dispatchers.IO) {
            runCatching {
                val url =
                    snapshotUrl.toHttpUrl().newBuilder()
                        .addQueryParameter("sessionId", sessionId)
                        .addQueryParameter("w", width.toString())
                        .addQueryParameter("h", height.toString())
                        .apply { if (!note.isNullOrBlank()) addQueryParameter("note", note) }
                        .build()
                val req =
                    Request.Builder()
                        .url(url)
                        .post(jpeg.toRequestBody("image/jpeg".toMediaType()))
                        .build()
                client.newCall(req).execute().use { resp ->
                    val txt = resp.body?.string().orEmpty()
                    check(resp.isSuccessful) { "snapshot HTTP ${resp.code}: $txt" }
                    Log.i(TAG, "snapshot accepted: $txt (${jpeg.size} bytes, ${width}x$height)")
                    txt
                }
            }
        }

    companion object {
        private const val TAG = "ForgeSnapshot"
    }
}
