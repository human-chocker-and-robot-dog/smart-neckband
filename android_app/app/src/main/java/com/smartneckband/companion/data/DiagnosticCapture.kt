package com.smartneckband.companion.data

import android.os.Build
import com.smartneckband.companion.BuildConfig
import com.smartneckband.companion.protocol.*
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.Instant
import java.util.UUID

/** No disk writes on the BLE/analysis path. Bounded recent evidence, exported explicitly. */
class DiagnosticCapture(
    private val clockMs: () -> Long = { System.nanoTime() / 1_000_000 },
    private val maxBytes: Int = 16 * 1024 * 1024,
    private val retentionMs: Long = 60_000,
) {
    private data class Entry(val at: Long, val line: String, val bytes: Int)
    private val entries = ArrayDeque<Entry>()
    private var size = 0
    private var evicted = 0L
    private var connection = UUID.randomUUID().toString()

    @Synchronized fun beginConnection() {
        connection = UUID.randomUUID().toString()
        add("state", JSONObject().put("state", "connecting"))
    }

    @Synchronized fun add(type: String, data: JSONObject) {
        val now = clockMs()
        val line = JSONObject().put("type", type).put("elapsed_ms", now)
            .put("connection_id", connection).put("data", data).toString()
        val bytes = line.toByteArray(Charsets.UTF_8).size
        entries.addLast(Entry(now, line, bytes))
        size += bytes
        trim(now)
    }

    private fun trim(now: Long) {
        while (entries.isNotEmpty() && (now - entries.first().at > retentionMs || size > maxBytes)) {
            size -= entries.removeFirst().bytes
            evicted++
        }
    }

    fun transport(bytes: ByteArray) {
        val alphabet = "0123456789abcdef"
        val hex = CharArray(bytes.size * 2)
        bytes.forEachIndexed { index, byte ->
            val n = byte.toInt() and 255
            hex[index * 2] = alphabet[n ushr 4]; hex[index * 2 + 1] = alphabet[n and 15]
        }
        add("transport", JSONObject().put("hex", String(hex)))
    }

    fun frame(frame: V0Frame, stats: ParserStats, accepted: Boolean) {
        val data = batchJson(frame).put("accepted", accepted).put("crc_errors", stats.crcErrors)
            .put("discarded_bytes", stats.discardedBytes).put("malformed_headers", stats.malformedHeaders)
            .put("parsed_frames", stats.frames)
        add("frame", data)
    }

    @Synchronized fun snapshotLines(metadata: JSONObject): List<String> {
        trim(clockMs())
        val header = JSONObject().put("type", "meta").put("schema", 1).put("platform", "android")
            .put("captured_at", Instant.now().toString()).put("source", "live_transport")
            .put("retention_ms", retentionMs).put("evicted_records", evicted).put("details", metadata)
        return listOf(header.toString()) + entries.map { it.line }
    }

    /** Caller runs on IO. A single most recent capture is retained across process death. */
    fun export(directory: File, ecgDemo: Boolean, insightDemo: Boolean): File {
        val lines = snapshotLines(JSONObject().put("app_version", BuildConfig.VERSION_NAME)
            .put("android", Build.VERSION.RELEASE).put("model", Build.MODEL)
            .put("neurokit2", "0.2.10").put("numpy", "1.23.3")
            .put("ecg_demo_display", ecgDemo).put("insight_demo_display", insightDemo))
        check(directory.isDirectory || directory.mkdirs()) { "无法创建诊断目录" }
        val temporary = File(directory, "latest.tmp")
        temporary.bufferedWriter(Charsets.UTF_8).use { writer -> lines.forEach { writer.appendLine(it) } }
        val target = File(directory, "latest.jsonl")
        java.nio.file.Files.move(temporary.toPath(), target.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING)
        return target
    }
}

fun batchJson(frame: V0Frame): JSONObject {
    val data = JSONObject().put("sequence", frame.sequence).put("timestamp", frame.timestampUs)
    return when (frame) {
        is V0Frame.Ecg -> data.put("kind", "ecg").put("first", frame.firstSampleIndex)
            .put("rate", frame.sampleRateHz).put("flags", frame.flags).put("samples", JSONArray(frame.samples.toList()))
        is V0Frame.Imu -> data.put("kind", "imu").put("first", frame.firstSampleIndex)
            .put("rate", frame.sampleRateHz).put("flags", frame.flags)
            .put("samples", JSONArray(frame.samples.map { listOf(it.ax.toInt(), it.ay.toInt(), it.az.toInt(), it.gx.toInt(), it.gy.toInt(), it.gz.toInt()) }))
        is V0Frame.Status -> data.put("kind", "status").put("lead_off_flags", frame.leadOffFlags)
            .put("sensor_status_flags", frame.sensorStatusFlags).put("errors", frame.errorCount)
            .put("ecg_overflow", frame.ecgRingOverflowCount).put("transport_drop", frame.transportDropCount)
        is V0Frame.ControlAck -> data.put("kind", "control_ack").put("request_id", frame.requestId)
            .put("active", frame.active).put("result", frame.result)
        else -> data.put("kind", "other")
    }
}
