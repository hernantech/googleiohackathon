package com.meta.spatial.samples.mixedrealitytemplate.forge.camera

import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import android.media.Image
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * YUV_420_888 → JPEG. Copied verbatim from the prior forge_quest client: the
 * stride-aware plane copy is the part that's easy to get wrong (many devices
 * return rowStride > width and pixelStride == 2 for chroma).
 */
object FrameEncoder {

    suspend fun yuvToJpeg(image: Image, quality: Int = 70): ByteArray =
        withContext(Dispatchers.Default) {
            require(image.format == ImageFormat.YUV_420_888) {
                "expected YUV_420_888, got format=${image.format}"
            }
            val width = image.width
            val height = image.height
            val nv21 = yuv420ToNv21(image, width, height)
            val yuv = YuvImage(nv21, ImageFormat.NV21, width, height, null)
            val out = ByteArrayOutputStream(width * height / 2)
            yuv.compressToJpeg(Rect(0, 0, width, height), quality, out)
            out.toByteArray()
        }

    private fun yuv420ToNv21(image: Image, width: Int, height: Int): ByteArray {
        val planes = image.planes
        val yPlane = planes[0]
        val uPlane = planes[1]
        val vPlane = planes[2]

        val ySize = width * height
        val chromaSize = ySize / 2
        val nv21 = ByteArray(ySize + chromaSize)

        copyYPlane(yPlane.buffer, yPlane.rowStride, yPlane.pixelStride, width, height, nv21, 0)

        val chromaWidth = width / 2
        val chromaHeight = height / 2
        interleaveChromaToNv21(
            uBuffer = uPlane.buffer,
            uRowStride = uPlane.rowStride,
            uPixelStride = uPlane.pixelStride,
            vBuffer = vPlane.buffer,
            vRowStride = vPlane.rowStride,
            vPixelStride = vPlane.pixelStride,
            chromaWidth = chromaWidth,
            chromaHeight = chromaHeight,
            out = nv21,
            outOffset = ySize,
        )
        return nv21
    }

    private fun copyYPlane(
        src: ByteBuffer,
        rowStride: Int,
        pixelStride: Int,
        width: Int,
        height: Int,
        out: ByteArray,
        outOffset: Int,
    ) {
        src.position(0)
        var dst = outOffset
        if (pixelStride == 1 && rowStride == width) {
            src.get(out, dst, width * height)
            return
        }
        val rowBuf = ByteArray(rowStride)
        for (row in 0 until height) {
            val bytesToRead = minOf(rowStride, src.remaining())
            src.get(rowBuf, 0, bytesToRead)
            if (pixelStride == 1) {
                System.arraycopy(rowBuf, 0, out, dst, width)
            } else {
                var srcIdx = 0
                for (col in 0 until width) {
                    out[dst + col] = rowBuf[srcIdx]
                    srcIdx += pixelStride
                }
            }
            dst += width
        }
    }

    private fun interleaveChromaToNv21(
        uBuffer: ByteBuffer,
        uRowStride: Int,
        uPixelStride: Int,
        vBuffer: ByteBuffer,
        vRowStride: Int,
        vPixelStride: Int,
        chromaWidth: Int,
        chromaHeight: Int,
        out: ByteArray,
        outOffset: Int,
    ) {
        uBuffer.position(0)
        vBuffer.position(0)
        val uRow = ByteArray(uRowStride)
        val vRow = ByteArray(vRowStride)
        var dst = outOffset
        for (row in 0 until chromaHeight) {
            val uBytes = minOf(uRowStride, uBuffer.remaining())
            uBuffer.get(uRow, 0, uBytes)
            val vBytes = minOf(vRowStride, vBuffer.remaining())
            vBuffer.get(vRow, 0, vBytes)
            var uIdx = 0
            var vIdx = 0
            for (col in 0 until chromaWidth) {
                out[dst++] = vRow[vIdx] // NV21: V then U
                out[dst++] = uRow[uIdx]
                uIdx += uPixelStride
                vIdx += vPixelStride
            }
        }
    }
}
