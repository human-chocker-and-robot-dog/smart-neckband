package com.smartneckband.companion

import android.graphics.Bitmap
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.test.platform.app.InstrumentationRegistry
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import com.smartneckband.companion.data.*
import com.smartneckband.companion.protocol.*
import com.smartneckband.companion.ui.CompanionUi
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Synthetic replay through the real framing, Python and Compose paths; no BLE connection. */
class StreamingReplayTest {
    @get:Rule val compose = createComposeRule()

    @Test fun missedSamplingPacketsStillProduceCompleteRawAndCleanTraces() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val app = instrumentation.targetContext.applicationContext as SmartCollarApplication
        if (!Python.isStarted()) Python.start(AndroidPlatform(app))
        val engine = Python.getInstance().getModule("collar_engine").callAttr("Engine")
        val fixture = JSONObject(instrumentation.context.assets.open("synthetic-ecg.json").bufferedReader().use { it.readText() })
        val samples = fixture.getJSONArray("samples")
        val parser = V0StreamParser()
        val capture = DiagnosticCapture()
        capture.beginConnection()
        for (packet in 0 until 250) {
            val bytes = ByteBuffer.allocate(68).order(ByteOrder.LITTLE_ENDIAN)
                .putShort(0x4E53).put(1).put(1).putShort(48)
                .putInt(packet).putLong(packet * 44_000L)
                .putInt(packet * 20).putShort(500).put(20).put(16) // SAMPLE_MISSED
            repeat(20) { bytes.putShort(samples.getInt(packet * 20 + it).toShort()) }
            bytes.putShort(crc16CcittFalse(bytes.array(), 66).toShort())
            // Mandatory ATT payload, including arbitrary protocol-frame boundaries.
            bytes.array().asList().chunked(20).forEach { chunk ->
                capture.transport(chunk.toByteArray())
                parser.feed(chunk.toByteArray()).forEach { decoded ->
                    val frame = decoded as V0Frame.Ecg
                    capture.frame(frame, parser.stats, true)
                    capture.add("processed", batchJson(frame))
                    engine.callAttr("append", JSONObject().put("kind", "ecg")
                        .put("first", frame.firstSampleIndex).put("timestamp", frame.timestampUs)
                        .put("flags", frame.flags).put("samples", JSONArray(frame.samples.toList())).toString())
                }
            }
        }
        val result = JSONObject(engine.callAttr("analyze").toString())
        capture.add("analysis", result)
        val lines = capture.snapshotLines(JSONObject().put("test", "StreamingReplayTest"))
        File(app.cacheDir, "synthetic-diagnostic.jsonl").bufferedWriter().use { writer ->
            writer.appendLine(JSONObject(lines.first()).put("source", "synthetic_test").toString())
            lines.drop(1).forEach { writer.appendLine(it) }
        }
        val raw = result.getJSONObject("raw").getJSONArray("values")
        assertEquals(samples.toString(), raw.toString())
        assertEquals(5000, result.getJSONObject("cleaned").getJSONArray("values").length())
        assertEquals(fixture.getDouble("bpm"), result.getDouble("bpm"), .05)
        assertEquals(fixture.getDouble("quality"), result.getDouble("quality"), .01)
        assertTrue(result.getBoolean("timing_warning"))
        assertTrue(result.isNull("rmssd"))
        assertEquals(0, result.getInt("hrv_window_s"))
        assertEquals(250L, parser.stats.frames)
        assertEquals(0L, parser.stats.crcErrors)
        app.repository.state(ConnectionState.CONNECTED, AcquisitionState.RUNNING)
        app.repository.analysis(result)
        try {
            compose.setContent {
                CompanionUi(app, "Synthetic replay", emptyList(), false, null,
                    onScan = {}, onSelect = {}, onStart = {}, onStop = {})
            }
            compose.onNodeWithText("查看原始波形与详情 →").performClick()
            compose.onNodeWithText("最近 5000 个采样 · 原始计数").assertIsDisplayed()
            compose.onNodeWithText("采样存在时序或削顶告警；心率供参考，HRV 暂停").assertIsDisplayed()
            compose.waitForIdle()
            compose.onRoot().captureToImage().asAndroidBitmap().let { bitmap ->
                File(app.cacheDir, "stream-replay.png").outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
            }
        } finally {
            app.repository.state(ConnectionState.DISCONNECTED, AcquisitionState.STOPPED)
        }
    }
}
