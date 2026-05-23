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
import java.util.concurrent.Executors
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
 * Quest 3/3S passthrough (world-facing) camera capture via the Camera2 +
 * Horizon OS Passthrough Camera API. Opens on demand, grabs one still, closes —
 * so it never contends with the passthrough compositor between snapshots.
 *
 * IMPORTANT: the world/bench-facing cameras report **LENS_FACING_BACK** on Quest
 * (verified via `dumpsys media.camera`: ids 50/51 = Back stereo pair; id 60 =
 * Front faces the wearer). The prior forge_quest client filtered FRONT, which
 * would capture the wearer's view — we select BACK here.
 *
 * Requires CAMERA + `horizonos.permission.HEADSET_CAMERA`; the latter is only
 * grantable via the in-VR runtime dialog (not adb).
 */
class PassthroughCapture(private val context: Context) {
    private val mutex = Mutex()

    /** Capture a single world-facing still as JPEG. Opens + closes the camera. */
    suspend fun captureStill(maxLongEdge: Int = 1280): Still =
        mutex.withLock {
            requirePermissions()
            val manager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
            val cameraId =
                pickWorldCameraId(manager)
                    ?: throw IllegalStateException("no BACK-facing passthrough camera found")
            val ch = manager.getCameraCharacteristics(cameraId)
            val size = chooseYuvSize(ch, maxLongEdge)
            Log.i(TAG, "capture: camera=$cameraId size=${size.first}x${size.second}")

            val thread = HandlerThread("ForgeCamera").apply { start() }
            val handler = Handler(thread.looper)
            val executor = Executors.newSingleThreadExecutor { r -> Thread(r, "ForgeCamSession") }
            val reader = ImageReader.newInstance(size.first, size.second, ImageFormat.YUV_420_888, 3)
            var device: CameraDevice? = null
            var session: CameraCaptureSession? = null
            try {
                val firstStable = awaitStableImage(reader, handler)
                device = openCamera(manager, cameraId, handler)
                session = createSession(device, reader.surface, executor)
                val req =
                    device.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply {
                        addTarget(reader.surface)
                        set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON)
                        set(
                            CaptureRequest.CONTROL_AF_MODE,
                            CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE,
                        )
                    }
                session.setRepeatingRequest(req.build(), null, handler)
                return@withLock firstStable.await() // suspends until a warmed frame is encoded
            } finally {
                try { session?.close() } catch (_: Throwable) {}
                try { device?.close() } catch (_: Throwable) {}
                try { reader.close() } catch (_: Throwable) {}
                try { thread.quitSafely() } catch (_: Throwable) {}
                executor.shutdown()
            }
        }

    private fun requirePermissions() {
        if (context.checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            throw SecurityException("CAMERA permission not granted")
        }
        if (context.checkSelfPermission(HEADSET_CAMERA) != PackageManager.PERMISSION_GRANTED) {
            throw SecurityException("$HEADSET_CAMERA not granted (grant in-VR)")
        }
    }

    /** Skip a few frames for AE to settle, then encode one. Completes the deferred. */
    private fun awaitStableImage(reader: ImageReader, handler: Handler): CompletableDeferred<Still> {
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
                val w = image.width
                val h = image.height
                try {
                    val jpeg = runBlocking { FrameEncoder.yuvToJpeg(image) }
                    deferred.complete(Still(jpeg, w, h))
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

    private fun pickWorldCameraId(manager: CameraManager): String? =
        manager.cameraIdList
            .filter { id ->
                val f = manager.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING)
                f == CameraCharacteristics.LENS_FACING_BACK
            }
            .minByOrNull { it.toIntOrNull() ?: Int.MAX_VALUE }

    private fun chooseYuvSize(ch: CameraCharacteristics, maxLongEdge: Int): Pair<Int, Int> {
        val map = ch.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
        val sizes = map?.getOutputSizes(ImageFormat.YUV_420_888)?.toList().orEmpty()
        if (sizes.isEmpty()) return 1280 to 960
        // Largest size whose long edge <= maxLongEdge; else the smallest available.
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
        executor: java.util.concurrent.Executor,
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
        private const val WARMUP_FRAMES = 3
    }
}
