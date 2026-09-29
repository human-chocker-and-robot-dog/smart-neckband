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
            analysisMessage = "演示 · 合成 ECG，非实际测量", hrvWindowSeconds = 60, rrCount = 71)
    }

    // These are UI examples, not medical interpretations or API responses.
    val cards = listOf(
        InsightCard("demo-rest", "一段安静的时光", "示例中，你已经安静坐了一会儿。可以先感受呼吸，再决定接下来做什么。",
            "演示 · 预设卡片", "demo-rest", Instant.parse("2026-09-29T01:20:00Z"),
            "静息 60 秒 · 心率 72 BPM · RMSSD 42 ms", "这些数值仅用于展示，不代表你的身体状态。"),
        InsightCard("demo-movement", "身体正在活动", "示例中，活动指数和心率一起上升。活动后可以给自己一点缓冲时间，再查看静息趋势。",
            "演示 · 预设卡片", "demo-movement", Instant.parse("2026-09-29T01:05:00Z"),
            "活动 3 分钟 · 心率 96 BPM · 活动指数 48", "活动期间不根据 HRV 判断压力。"),
        InsightCard("demo-recovery", "节奏慢慢平稳", "示例中，活动结束后的心率由 96 降到 76 BPM。卡片展示变化经过，不对恢复能力作结论。",
            "演示 · 预设卡片", "demo-recovery", Instant.parse("2026-09-29T00:55:00Z"),
            "活动后 2 分钟 · 96 → 76 BPM", "个人趋势需要多次、相近条件下的记录。"),
        InsightCard("demo-quality", "这一段，先不解释", "示例中，电极接触不稳定。先调整佩戴，等波形稳定后再看心率与 HRV。",
            "演示 · 预设卡片", "demo-quality", Instant.parse("2026-09-29T00:40:00Z"),
            "导联接触告警 · 信号质量不足", "数据不足时不生成身体状态结论。"),
    )
}
