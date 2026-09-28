package com.smartneckband.companion.protocol

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

class V0ProtocolTest {
    private fun vectors(file: String) = JSONObject(File(System.getProperty("repository.root"), "docs/protocol/$file").readText()).getJSONArray("vectors")
    private fun hex(value: String) = value.chunked(2).map { it.toInt(16).toByte() }.toByteArray()

    @Test fun sharedGoldenVectorsAtEverySplit() {
        val vectors = vectors("v0_golden_vectors.json")
        for (index in 0 until vectors.length()) {
            val vector = vectors.getJSONObject(index)
            val packet = hex(vector.getString("packet_hex"))
            for (split in 0..packet.size) {
                val parser = V0StreamParser()
                val frames = parser.feed(packet.copyOfRange(0, split)) + parser.feed(packet.copyOfRange(split, packet.size))
                assertEquals(1, frames.size)
                assertEquals(vector.getLong("packet_sequence"), frames.single().sequence)
                when (val frame = frames.single()) {
                    is V0Frame.Ecg -> { assertEquals(500, frame.sampleRateHz); assertEquals(2048, frame.samples.first()); assertEquals(2067, frame.samples.last()) }
                    is V0Frame.Imu -> assertEquals(-1000, frame.samples.first().ay.toInt())
                    is V0Frame.Status -> { assertEquals(3L, frame.errorCount); assertEquals(5L, frame.i2cErrorCount); assertEquals(97, frame.statusFlags) }
                    else -> Unit
                }
            }
        }
    }

    @Test fun crcFailureAndNoiseRecoverWithoutFabricatingSamples() {
        val good = hex(vectors("v0_golden_vectors.json").getJSONObject(0).getString("packet_hex"))
        val corrupt = good.copyOf().also { it[30] = (it[30].toInt() xor 1).toByte() }
        val parser = V0StreamParser()
        val received = (byteArrayOf(1, 2, 3) + corrupt + good + good).flatMap { parser.feed(byteArrayOf(it)) }
        assertEquals(2, received.size)
        assertEquals(1L, parser.stats.crcErrors)
        assertTrue(parser.stats.discardedBytes >= corrupt.size + 3)
    }

    @Test fun rejectsBadRateEvenWithValidCrc() {
        val packet = hex(vectors("v0_golden_vectors.json").getJSONObject(0).getString("packet_hex"))
        packet[22] = 0; packet[23] = 0
        val crc = crc16CcittFalse(packet, packet.size - 2)
        packet[packet.size - 2] = crc.toByte(); packet[packet.size - 1] = (crc shr 8).toByte()
        assertTrue(V0StreamParser().feed(packet).isEmpty())
    }

    @Test fun sharedControlVectors() {
        val vectors = vectors("acquisition_control_golden_vectors.json")
        for (i in 0 until vectors.length()) {
            val vector = vectors.getJSONObject(i)
            val bytes = hex(vector.getString("packet_hex"))
            if (vector.getInt("packet_type") == 7) {
                assertArrayEquals(bytes, acquisitionCommand(vector.getLong("request_id"), vector.getInt("operation")))
            } else {
                val ack = V0StreamParser().feed(bytes).single() as V0Frame.ControlAck
                assertEquals(vector.getLong("request_id"), ack.requestId)
                assertEquals(vector.getBoolean("active"), ack.active)
            }
        }
    }

    @Test fun micPayloadContainingValidEcgIsNotReportedAsEcg() {
        val good = hex(vectors("v0_golden_vectors.json").getJSONObject(0).getString("packet_hex"))
        val mic = ByteBuffer.allocate(29 + good.size + 2).order(ByteOrder.LITTLE_ENDIAN)
            .put("MIC1".toByteArray()).put(1).put(1).put(1).putShort(0).putInt(1)
            .putInt(16000).putLong(0).putShort((good.size / 2).toShort()).putShort(good.size.toShort())
            .put(good).array()
        ByteBuffer.wrap(mic).order(ByteOrder.LITTLE_ENDIAN).putShort(mic.size - 2, crc16CcittFalse(mic, mic.size - 2).toShort())
        for (chunkSize in 1..23) {
            val parser = V0StreamParser()
            val frames = (mic + good).asList().chunked(chunkSize).flatMap { parser.feed(it.toByteArray()) }
            assertEquals(1, frames.size)
            assertEquals(1L, parser.stats.skippedMicFrames)
        }
    }
}
