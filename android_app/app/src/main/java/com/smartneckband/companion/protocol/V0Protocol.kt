package com.smartneckband.companion.protocol

import java.nio.ByteBuffer
import java.nio.ByteOrder

private const val HEADER_SIZE = 18
private const val CRC_SIZE = 2
private const val MAGIC_0: Byte = 0x53
private const val MAGIC_1: Byte = 0x4E
private const val VERSION = 1
private const val ECG_TYPE = 1
private const val IMU_TYPE = 2
private const val STATUS_TYPE = 3
private val PAYLOAD_LENGTHS = mapOf(1 to 48, 2 to 32, 3 to 32, 4 to 48, 5 to 20, 6 to 8, 7 to 8, 8 to 8)
private val MIC_MAGIC = "MIC1".toByteArray(Charsets.US_ASCII)

sealed interface V0Frame {
    val sequence: Long
    val timestampUs: Long

    data class Ecg(
        override val sequence: Long,
        override val timestampUs: Long,
        val firstSampleIndex: Long,
        val sampleRateHz: Int,
        val flags: Int,
        val samples: IntArray,
    ) : V0Frame

    data class Imu(
        override val sequence: Long,
        override val timestampUs: Long,
        val firstSampleIndex: Long,
        val sampleRateHz: Int,
        val flags: Int,
        val samples: List<ImuPoint>,
    ) : V0Frame

    data class Status(
        override val sequence: Long,
        override val timestampUs: Long,
        val leadOffFlags: Int,
        val sensorStatusFlags: Int,
        val ecgBufferUsagePercent: Int,
        val imuBufferUsagePercent: Int,
        val transportQueueUsagePercent: Int,
        val statusFlags: Int,
        val errorCount: Long,
        val ecgRingOverflowCount: Long,
        val imuRingOverflowCount: Long,
        val transportOverflowCount: Long,
        val transportDropCount: Long,
        val i2cErrorCount: Long,
    ) : V0Frame

    data class ControlAck(override val sequence: Long, override val timestampUs: Long,
        val requestId: Long, val active: Boolean, val result: Int) : V0Frame

    data class Other(override val sequence: Long, override val timestampUs: Long, val type: Int) : V0Frame
}

data class ImuPoint(val ax: Short, val ay: Short, val az: Short, val gx: Short, val gy: Short, val gz: Short)

data class ParserStats(
    val frames: Long = 0,
    val crcErrors: Long = 0,
    val malformedHeaders: Long = 0,
    val discardedBytes: Long = 0,
    val skippedMicFrames: Long = 0,
)

/** Incremental parser for the transport-independent V0 stream. */
class V0StreamParser {
    private val buffer = ArrayDeque<Byte>()
    var stats: ParserStats = ParserStats()
        private set

    fun reset() {
        buffer.clear()
        stats = ParserStats()
    }

    fun feed(chunk: ByteArray): List<V0Frame> {
        chunk.forEach(buffer::addLast)
        val frames = mutableListOf<V0Frame>()
        while (buffer.size >= 2) {
            val magicAt = findMagic()
            if (magicAt < 0) {
                val bytes = buffer.toByteArray()
                val keep = (minOf(3, bytes.size) downTo 1).firstOrNull { n ->
                    val suffix = bytes.takeLast(n)
                    suffix == MIC_MAGIC.take(n) || suffix == listOf(MAGIC_0)
                } ?: 0
                val discarded = buffer.size - keep
                repeat(discarded) { buffer.removeFirst() }
                stats = stats.copy(discardedBytes = stats.discardedBytes + discarded)
                break
            }
            repeat(magicAt) { buffer.removeFirst() }
            stats = stats.copy(discardedBytes = stats.discardedBytes + magicAt)
            // Skip an entire validated MIC1 frame. A V0-looking byte sequence
            // inside microphone payload must never be decoded as ECG.
            if (buffer.first() == MIC_MAGIC.first()) {
                if (buffer.size < 29) break
                val bytes = buffer.toByteArray()
                val mic = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
                val payloadLength = mic.getShort(27).toInt() and 0xFFFF
                if (bytes[4].toInt() != 1 || bytes[5].toInt() !in 1..3 || mic.getInt(13) != 16000 || payloadLength > 1024) {
                    buffer.removeFirst()
                    stats = stats.copy(malformedHeaders = stats.malformedHeaders + 1, discardedBytes = stats.discardedBytes + 1)
                    continue
                }
                val length = 29 + payloadLength + 2
                if (buffer.size < length) break
                val expected = mic.getShort(length - 2).toInt() and 0xFFFF
                if (expected != crc16CcittFalse(bytes, length - 2)) {
                    buffer.removeFirst()
                    stats = stats.copy(crcErrors = stats.crcErrors + 1, discardedBytes = stats.discardedBytes + 1)
                } else {
                    repeat(length) { buffer.removeFirst() }
                    stats = stats.copy(skippedMicFrames = stats.skippedMicFrames + 1)
                }
                continue
            }
            if (buffer.size < HEADER_SIZE) break
            val header = buffer.toByteArray().copyOf(HEADER_SIZE)
            val view = ByteBuffer.wrap(header).order(ByteOrder.LITTLE_ENDIAN)
            val magic = view.short.toInt() and 0xFFFF
            val version = view.get().toInt() and 0xFF
            val type = view.get().toInt() and 0xFF
            val payloadLength = view.short.toInt() and 0xFFFF
            val sequence = view.int.toLong() and 0xFFFF_FFFFL
            val timestampUs = view.long
            if (magic != 0x4E53 || version != VERSION || payloadLength != PAYLOAD_LENGTHS[type]) {
                buffer.removeFirst()
                stats = stats.copy(malformedHeaders = stats.malformedHeaders + 1, discardedBytes = stats.discardedBytes + 1)
                continue
            }
            val frameLength = HEADER_SIZE + payloadLength + CRC_SIZE
            if (buffer.size < frameLength) break
            val bytes = buffer.toByteArray().copyOf(frameLength)
            val expected = ByteBuffer.wrap(bytes, frameLength - 2, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF
            val actual = crc16CcittFalse(bytes, frameLength - 2)
            if (expected != actual) {
                buffer.removeFirst()
                stats = stats.copy(crcErrors = stats.crcErrors + 1, discardedBytes = stats.discardedBytes + 1)
                continue
            }
            repeat(frameLength) { buffer.removeFirst() }
            val parsed = parse(type, sequence, timestampUs, bytes.copyOfRange(HEADER_SIZE, HEADER_SIZE + payloadLength))
            if (parsed != null) {
                frames += parsed
                stats = stats.copy(frames = stats.frames + 1)
            } else {
                stats = stats.copy(malformedHeaders = stats.malformedHeaders + 1)
            }
        }
        return frames
    }

    private fun findMagic(): Int {
        val bytes = buffer.toByteArray()
        for (i in 0 until bytes.size - 1) {
            if (bytes[i] == MAGIC_0 && bytes[i + 1] == MAGIC_1) return i
            if (i + 4 <= bytes.size && (0..3).all { bytes[i + it] == MIC_MAGIC[it] }) return i
        }
        return -1
    }

    private fun parse(type: Int, sequence: Long, timestampUs: Long, payload: ByteArray): V0Frame? {
        val view = ByteBuffer.wrap(payload).order(ByteOrder.LITTLE_ENDIAN)
        return when (type) {
            ECG_TYPE -> {
                if (payload.size < 8) return null
                val first = view.int.toLong() and 0xFFFF_FFFFL
                val rate = view.short.toInt() and 0xFFFF
                val count = view.get().toInt() and 0xFF
                val flags = view.get().toInt() and 0xFF
                if (count != 20 || rate != 500 || payload.size != 8 + count * 2) return null
                val samples = IntArray(count) { view.short.toInt() and 0xFFFF }
                V0Frame.Ecg(sequence, timestampUs, first, rate, flags, samples)
            }
            IMU_TYPE -> {
                if (payload.size < 8) return null
                val first = view.int.toLong() and 0xFFFF_FFFFL
                val rate = view.short.toInt() and 0xFFFF
                val count = view.get().toInt() and 0xFF
                val flags = view.get().toInt() and 0xFF
                if (count != 2 || rate != 50 || payload.size != 8 + count * 12) return null
                val points = (0 until count).map {
                    ImuPoint(view.short, view.short, view.short, view.short, view.short, view.short)
                }
                V0Frame.Imu(sequence, timestampUs, first, rate, flags, points)
            }
            STATUS_TYPE -> {
                if (payload.size < 32) return null
                V0Frame.Status(
                    sequence, timestampUs,
                    view.get().toInt() and 0xFF,
                    view.get().toInt() and 0xFF,
                    view.get().toInt() and 0xFF,
                    view.get().toInt() and 0xFF,
                    view.get().toInt() and 0xFF,
                    run { view.get(); view.short.toInt() and 0xFFFF },
                    view.int.toLong() and 0xFFFF_FFFFL,
                    view.int.toLong() and 0xFFFF_FFFFL,
                    view.int.toLong() and 0xFFFF_FFFFL,
                    view.int.toLong() and 0xFFFF_FFFFL,
                    view.int.toLong() and 0xFFFF_FFFFL,
                    view.int.toLong() and 0xFFFF_FFFFL,
                )
            }
            8 -> {
                val id = view.int.toLong() and 0xFFFF_FFFFL
                val active = view.get().toInt() and 0xFF
                val result = view.get().toInt() and 0xFF
                if (active !in 0..1 || result !in 0..1 || view.short.toInt() != 0) return null
                V0Frame.ControlAck(sequence, timestampUs, id, active == 1, result)
            }
            else -> V0Frame.Other(sequence, timestampUs, type)
        }
    }
}

/** START=1, STOP=0, QUERY=2. A GATT write completion is not a device ACK. */
fun acquisitionCommand(requestId: Long, operation: Int): ByteArray {
    require(operation in 0..2)
    val bytes = ByteBuffer.allocate(28).order(ByteOrder.LITTLE_ENDIAN)
        .putShort(0x4E53).put(1).put(7).putShort(8).putInt(0).putLong(0)
        .putInt(requestId.toInt()).put(operation.toByte()).put(0).putShort(0).array()
    ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).putShort(26, crc16CcittFalse(bytes, 26).toShort())
    return bytes
}

fun crc16CcittFalse(bytes: ByteArray, length: Int = bytes.size): Int {
    var crc = 0xFFFF
    for (index in 0 until length) {
        crc = crc xor ((bytes[index].toInt() and 0xFF) shl 8)
        repeat(8) { crc = if ((crc and 0x8000) != 0) ((crc shl 1) xor 0x1021) and 0xFFFF else (crc shl 1) and 0xFFFF }
    }
    return crc
}
