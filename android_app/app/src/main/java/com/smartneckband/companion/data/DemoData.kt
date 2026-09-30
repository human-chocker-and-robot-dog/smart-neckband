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
        InsightCard("demo-rhythm", "心率与 RMSSD 的含义不同", "示例心率为 72 BPM，RMSSD 参考值为 42 ms；心率描述心搏频率，RMSSD 描述相邻心搏间期变化，属于两个维度。",
            "演示 · 预设卡片", "demo-everyday", Instant.parse("2026-09-29T01:20:00Z"),
            "心率 72 BPM\nRMSSD ≈ 42 ms", "演示 · 合成数值，不代表你的身体状态。", kind = "rhythm", schemaVersion = 3),
        InsightCard("demo-activity", "静止不等于完全不动", "静止占比表示近期低活动桶占比，即使为 96% 仍可能有轻微动作；它不能说明坐姿、睡眠或情绪。",
            "演示 · 预设卡片", "demo-everyday", Instant.parse("2026-09-29T01:20:00Z"),
            "活动指数 4\n静止占比 96%", "演示 · 合成数值，不代表你的身体状态。", kind = "activity", schemaVersion = 3),
        InsightCard("demo-suggestion", "无需追着数字调整", "活动幅度已经较小，这次更适合按原状态继续记录，不为降低数字刻意改变动作。",
            "演示 · 预设卡片", "demo-everyday", Instant.parse("2026-09-29T01:20:00Z"),
            "心率 72 BPM\n活动指数 4", "演示 · 合成数值，不代表你的身体状态。",
            suggestion = "继续手头轻量任务，不必为了某个数字刻意休息或运动。", kind = "suggestion", schemaVersion = 3),
    )
}
