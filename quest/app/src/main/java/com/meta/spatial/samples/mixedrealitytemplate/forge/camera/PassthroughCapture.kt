package com.meta.spatial.samples.mixedrealitytemplate.forge.camera

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.ImageFormat
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.params.OutputConfiguration
import android.hardware.camera2.params.SessionConfiguration
import android.media.Image
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import java.util.concurrent.Executor
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicLong
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** A captured still: JPEG bytes + pixel dimensions. */
data class Still(val jpeg: ByteArray, val width: Int, val height: Int)

/**
 * Quest 3/3S passthrough (world-facing) camera via Camera2 + Horizon OS PCA.
 * Two modes that share one physical camera (never opened twice at once):
 *  - [captureStill]: open → grab one still → close (for /v2/snapshot).
 *  - [startStreaming]/[stopStreaming]: hold the camera open and emit JPEG frames
 *    at ~fps (for /v2/live video). While streaming, [captureStill] returns the
 *    latest streamed frame instead of opening a second session.
 *
 * Camera coexists with passthrough rendering (a system recording indicator
 * shows). Requires CAMERA + horizonos.permission.HEADSET_CAMERA (in-VR grant).
 */
class PassthroughCapture(private val context: Context) {
    private val mutex = Mutex()

    @Volatile var streamingActive = false
        private set

    @Volatile private var lastStill: Still? = null
    private var streamDevice: CameraDevice? = null
    private var streamSession: CameraCaptureSession? = null
    private var streamReader: ImageReader? = null
    private var streamThread: HandlerThread? = null
    private var streamExecutor: java.util.concurrent.ExecutorService? = null

    /** Most recent frame produced while streaming (used by the snapshot path during live). */
    fun lastStreamedStill(): Still? = lastStill

    /** Single world-facing still as JPEG. Opens + closes the camera (unless streaming). */
    suspend fun captureStill(maxLongEdge: Int = 1280): Still {
        if (streamingActive) {
            return lastStill ?: throw IllegalStateException("live stream warming up; no frame yet")
        }
        return mutex.withLock {
            requirePermissions()
            val manager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
            val cameraId = pickWorldCameraId(manager) ?: throw IllegalStateException("no world camera")
            val ch = manager.getCameraCharacteristics(cameraId)
            val size = chooseYuvSize(ch, maxLongEdge)
            Log.i(TAG, "still: camera=$cameraId size=${size.first}x${size.second}")

            val thread = HandlerThread("ForgeCamStill").apply { start() }
            val handler = Handler(thread.looper)
            val executor = Executors.newSingleThreadExecutor { r -> Thread(r, "ForgeCamStillSession") }
            val reader = ImageReader.newInstance(size.first, size.second, ImageFormat.YUV_420_888, 3)
            var device: CameraDevice? = null
            var session: CameraCaptureSession? = null
            try {
                val firstStable = encodeAfterWarmup(reader, handler)
                device = openCamera(manager, cameraId, handler)
                session = createSession(device, reader.surface, executor)
                session.setRepeatingRequest(previewRequest(device, reader).build(), null, handler)
                kotlinx.coroutines.withTimeout(6_000) { firstStable.await() }
            } finally {
                try { session?.close() } catch (_: Throwable) {}
                try { device?.close() } catch (_: Throwable) {}
                try { reader.close() } catch (_: Throwable) {}
                try { thread.quitSafely() } catch (_: Throwable) {}
                executor.shutdown()
            }
        }
    }

    /** Open the camera and emit downscaled JPEG frames at ~[fps] until [stopStreaming]. */
    suspend fun startStreaming(fps: Double = 2.0, maxLongEdge: Int = 512, onJpeg: (ByteArray) -> Unit) {
        if (streamingActive) return
        requirePermissions()
        val manager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
        val cameraId = pickWorldCameraId(manager) ?: throw IllegalStateException("no world camera")
        val ch = manager.getCameraCharacteristics(cameraId)
        val size = chooseYuvSize(ch, maxLongEdge)
        Log.i(TAG, "stream: camera=$cameraId size=${size.first}x${size.second} @${fps}fps")

        val thread = HandlerThread("ForgeCamStream").apply { start() }
        val handler = Handler(thread.looper)
        val executor = Executors.newSingleThreadExecutor { r -> Thread(r, "ForgeCamStreamSession") }
        val reader = ImageReader.newInstance(size.first, size.second, ImageFormat.YUV_420_888, 3)
        val intervalNs = (1_000_000_000.0 / fps.coerceAtLeast(0.5)).toLong()
        val lastEmit = AtomicLong(0)

        reader.setOnImageAvailableListener(
            { r ->
                val image: Image =
                    try {
                        r.acquireLatestImage()
                    } catch (_: IllegalStateException) {
                        null
                    } ?: return@setOnImageAvailableListener
                val now = System.nanoTime()
                if (now - lastEmit.get() < intervalNs) {
                    image.close()
                    return@setOnImageAvailableListener
                }
                lastEmit.set(now)
                try {
                    val jpeg = runBlocking { FrameEncoder.yuvToJpeg(image, quality = 60) }
                    lastStill = Still(jpeg, image.width, image.height)
                    onJpeg(jpeg)
                } catch (t: Throwable) {
                    Log.w(TAG, "stream frame encode failed: ${t.message}")
                } finally {
                    try { image.close() } catch (_: Throwable) {}
                }
            },
            handler,
        )

        val device = openCamera(manager, cameraId, handler)
        val session = createSession(device, reader.surface, executor)
        session.setRepeatingRequest(previewRequest(device, reader).build(), null, handler)
        streamDevice = device
        streamSession = session
        streamReader = reader
        streamThread = thread
        streamExecutor = executor
        streamingActive = true
    }

    fun stopStreaming() {
        streamingActive = false
        try { streamSession?.close() } catch (_: Throwable) {}
        try { streamDevice?.close() } catch (_: Throwable) {}
        try { streamReader?.close() } catch (_: Throwable) {}
        try { streamThread?.quitSafely() } catch (_: Throwable) {}
        streamExecutor?.shutdown()
        streamSession = null
        streamDevice = null
        streamReader = null
        streamThread = null
        streamExecutor = null
    }

    private fun previewRequest(device: CameraDevice, reader: ImageReader): CaptureRequest.Builder =
        device.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply {
            addTarget(reader.surface)
            set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON)
            set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
        }

    private fun requirePermissions() {
        if (context.checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            throw SecurityException("CAMERA permission not granted")
        }
        if (context.checkSelfPermission(HEADSET_CAMERA) != PackageManager.PERMISSION_GRANTED) {
            throw SecurityException("$HEADSET_CAMERA not granted (grant in-VR)")
        }
    }

    /** Skip a few frames for AE to settle, then encode one and complete the deferred. */
    private fun encodeAfterWarmup(reader: ImageReader, handler: Handler): CompletableDeferred<Still> {
        val deferred = CompletableDeferred<Still>()
        var seen = 0
        reader.setOnImageAvailableListener(
            { r ->
                val image: Image =
                    try {
                        r.acquireLatestImage()
                    } catch (_: IllegalStateException) {
                        null
                    } ?: return@setOnImageAvailableListener
                seen++
                if (deferred.isCompleted || seen < WARMUP_FRAMES) {
                    image.close()
                    return@setOnImageAvailableListener
                }
                try {
                    val jpeg = runBlocking { FrameEncoder.yuvToJpeg(image) }
                    deferred.complete(Still(jpeg, image.width, image.height))
                } catch (t: Throwable) {
                    deferred.completeExceptionally(t)
                } finally {
                    try { image.close() } catch (_: Throwable) {}
                }
            },
            handler,
        )
        return deferred
    }

    private fun pickWorldCameraId(manager: CameraManager): String? {
        val ids = manager.cameraIdList
        fun meta(id: String, key: CameraCharacteristics.Key<Int>): Int? =
            runCatching { manager.getCameraCharacteristics(id).get(key) }.getOrNull()

        val passthrough = ids.filter { meta(it, SOURCE_KEY) == CAMERA_SOURCE_PASSTHROUGH }
        passthrough.minByOrNull { meta(it, POSITION_KEY) ?: 99 }?.let { return it }

        Log.w(TAG, "no PCA vendor-tag camera; falling back to LENS_FACING_BACK")
        ids.filter {
            runCatching {
                manager.getCameraCharacteristics(it).get(CameraCharacteristics.LENS_FACING) ==
                    CameraCharacteristics.LENS_FACING_BACK
            }.getOrDefault(false)
        }.minByOrNull { it.toIntOrNull() ?: Int.MAX_VALUE }?.let { return it }

        return ids.firstOrNull()
    }

    private fun chooseYuvSize(ch: CameraCharacteristics, maxLongEdge: Int): Pair<Int, Int> {
        val map = ch.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
        val sizes = map?.getOutputSizes(ImageFormat.YUV_420_888)?.toList().orEmpty()
        if (sizes.isEmpty()) return 1280 to 960
        val capped = sizes.filter { maxOf(it.width, it.height) <= maxLongEdge }
        val pick = (capped.maxByOrNull { it.width * it.height } ?: sizes.minByOrNull { it.width * it.height })!!
        return pick.width to pick.height
    }

    private suspend fun openCamera(
        manager: CameraManager,
        cameraId: String,
        handler: Handler,
    ): CameraDevice = suspendCancellableCoroutine { cont ->
        try {
            manager.openCamera(
                cameraId,
                object : CameraDevice.StateCallback() {
                    override fun onOpened(device: CameraDevice) {
                        if (cont.isActive) cont.resume(device)
                    }

                    override fun onDisconnected(device: CameraDevice) {
                        device.close()
                        if (cont.isActive) cont.resumeWithException(IllegalStateException("disconnected"))
                    }

                    override fun onError(device: CameraDevice, error: Int) {
                        device.close()
                        if (cont.isActive) cont.resumeWithException(IllegalStateException("camera error $error"))
                    }
                },
                handler,
            )
        } catch (t: Throwable) {
            if (cont.isActive) cont.resumeWithException(t)
        }
    }

    private suspend fun createSession(
        device: CameraDevice,
        surface: android.view.Surface,
        executor: Executor,
    ): CameraCaptureSession = suspendCancellableCoroutine { cont ->
        val config =
            SessionConfiguration(
                SessionConfiguration.SESSION_REGULAR,
                listOf(OutputConfiguration(surface)),
                executor,
                object : CameraCaptureSession.StateCallback() {
                    override fun onConfigured(s: CameraCaptureSession) {
                        if (cont.isActive) cont.resume(s)
                    }

                    override fun onConfigureFailed(s: CameraCaptureSession) {
                        if (cont.isActive) cont.resumeWithException(IllegalStateException("session configure failed"))
                    }
                },
            )
        try {
            device.createCaptureSession(config)
        } catch (t: Throwable) {
            if (cont.isActive) cont.resumeWithException(t)
        }
    }

    companion object {
        private const val TAG = "ForgeCamera"
        const val HEADSET_CAMERA = "horizonos.permission.HEADSET_CAMERA"
        private const val WARMUP_FRAMES = 8
        private const val CAMERA_SOURCE_PASSTHROUGH = 0
        private val SOURCE_KEY =
            CameraCharacteristics.Key("com.meta.extra_metadata.camera_source", Int::class.java)
        private val POSITION_KEY =
            CameraCharacteristics.Key("com.meta.extra_metadata.position", Int::class.java)
    }
}
