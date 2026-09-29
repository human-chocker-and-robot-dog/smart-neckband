package com.smartneckband.companion.data

import java.time.Instant
import kotlin.math.*

/** Hand-authored presentation fixtures. Never feed these into acquisition or InsightEngine. */
object DemoData {
    fun snapshot(elapsedSeconds: Double = 0.0): CollarSnapshot {
        fun pulse(x: Double, center: Double, width: Double, height: Double) =
            height * exp(-((x - center) / width).pow(2))
        val clean = (0 until 2500).map { index ->
            val phase = ((index / 500.0 + elapsedSeconds) % (60.0 / 72.0))
            (pulse(phase, .14, .035, 65.0) - pulse(phase, .29, .010, 110.0) +
                pulse(phase, .31, .012, 700.0) - pulse(phase, .34, .018, 180.0) +
                pulse(phase, .54, .065, 160.0)).toFloat()
        }
        val times = clean.indices.map { it / 500f }
        val raw = clean.mapIndexed { index, value ->
            (2048 + value + 55 * sin(2 * PI * .3 * (index / 500.0 + elapsedSeconds)) +
                18 * sin(2 * PI * 50 * (index / 500.0 + elapsedSeconds))).toFloat()
        }
        return CollarSnapshot(acquisition = AcquisitionState.RUNNING,
            heart = HeartSnapshot(72.0, 42.0, .94, false, Instant.now()),
            imu = ImuSnapshot(4.0, 96.0, "still", true, Instant.now()), dataAgeMs = 0,
            rawEcg = EcgWaveform(raw, times), cleanEcg = EcgWaveform(clean, times),
            ecgSampleCount = clean.size, analysisSampleCount = clean.size, effectiveSampleRateHz = 500.0,
            analysisMessage = "演示 · 合成 ECG，非实际测量", hrvWindowSeconds = 60, rrCount = 71,
            hrvStatus = "demo", hrvReasons = listOf("演示 RMSSD，非实际测量"))
    }

    // These are UI examples, not medical interpretations or API responses.
    val cards = listOf(
        InsightCard("demo-rhythm", "听见此刻的节奏", "示例里的心率是 72 BPM，RMSSD 参考值是 42 ms。这两个数字为当下留下一张身体小记，可以结合此刻的感受慢慢读。",
            "演示 · 预设卡片", "demo-everyday", Instant.parse("2026-09-29T01:20:00Z"),
            "心率 72 BPM\nRMSSD ≈ 42 ms", "演示 · 合成数值，不代表你的身体状态。", kind = "rhythm", schemaVersion = 2),
        InsightCard("demo-activity", "一小段安静的空隙", "示例里的身体活动幅度较小。这样的间隙，适合把注意力放回肩膀、坐姿和呼吸，给自己留一点舒适的空间。",
            "演示 · 预设卡片", "demo-everyday", Instant.parse("2026-09-29T01:20:00Z"),
            "活动指数 4\n静止占比 96%", "演示 · 合成数值，不代表你的身体状态。", kind = "activity", schemaVersion = 2),
        InsightCard("demo-suggestion", "给自己一分钟", "把这一刻的记录当成一个轻柔的提醒。接下来的小片刻，可以留给自己，不必急着赶往下一件事。",
            "演示 · 预设卡片", "demo-everyday", Instant.parse("2026-09-29T01:20:00Z"),
            "心率 72 BPM\n活动指数 4", "演示 · 合成数值，不代表你的身体状态。",
            suggestion = "放松肩膀，自然呼吸，感受一分钟的当下。", kind = "suggestion", schemaVersion = 2),
    )
}
