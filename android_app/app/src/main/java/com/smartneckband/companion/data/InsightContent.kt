package com.smartneckband.companion.data

import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.util.Locale
import java.util.UUID

/** Everyday observations use the fresh displayed metrics, with references explicitly identified. */
object InsightContent {
    fun liveEvent(snapshot: CollarSnapshot): JSONObject? {
        if (snapshot.acquisition != AcquisitionState.RUNNING || snapshot.dataAgeMs?.let { it in 0..3000 } != true) return null
        val now = Instant.now()
        fun fresh(at: Instant?) = at?.let { now.toEpochMilli() - it.toEpochMilli() in 0..5000 } == true
        val heartFresh = fresh(snapshot.heart.observedAt)
        val imuFresh = snapshot.imu.online && fresh(snapshot.imu.observedAt)
        val bpm = snapshot.heart.bpm?.takeIf { heartFresh && it.isFinite() && it > 0 }
        val hrv = snapshot.displayHrvRmssdMs?.takeIf { heartFresh && it.isFinite() && it >= 0 }
        val motion = snapshot.imu.motionScore?.takeIf { imuFresh && it.isFinite() && it >= 0 }
        val still = snapshot.imu.stillRatioPercent?.takeIf { imuFresh && it.isFinite() && it in 0.0..100.0 }
        val metrics = JSONObject()
        val evidence = JSONObject()
        val references = JSONArray()
        fun metric(id: String, value: Double?, label: String, unit: String, reference: Boolean = false) {
            if (value == null) return
            metrics.put(id, value)
            evidence.put(id, "$label ${if (reference) "≈ " else ""}${number(value)}$unit")
            if (reference) references.put(id)
        }
        metric("heart_rate_bpm", bpm, "心率", " BPM")
        metric("hrv_rmssd_ms", hrv, "RMSSD", " ms", snapshot.isHrvReference)
        metric("motion_score", motion, "活动指数", "")
        metric("still_ratio_percent", still, "静止占比", "%")
        if (metrics.length() == 0) return null
        val type = when {
            motion != null && motion >= 35 -> "body.movement"
            motion != null && motion < 10 -> "body.rest_window"
            else -> "body.observation"
        }
        val context = JSONObject().put("comparison", "single_snapshot").put("reference_metrics", references)
            .put("motion_window_seconds", 30.0)
            .put("motion_still_threshold", 10.0)
            .put("motion_score_range", "0-100; higher means greater recent movement amplitude")
        if (hrv != null) context.put("hrv_window_seconds", if (snapshot.isHrvReference)
            snapshot.hrvReferenceWindowSeconds else snapshot.hrvWindowSeconds)
            .put("hrv_rr_count", if (snapshot.isHrvReference) snapshot.hrvReferenceRrCount else snapshot.rrCount)
        val definitions = JSONObject()
            .put("heart_rate_bpm", "当前心率，单位 BPM，表示心搏频率")
            .put("hrv_rmssd_ms", "RMSSD，单位 ms，表示相邻心搏间期差值的均方根")
            .put("motion_score", "最近最多 30 秒 IMU 活动指数；0–100，数值越大表示动作幅度越大")
            .put("still_ratio_percent", "最近最多 30 秒内低活动桶所占比例；100% 不等于完全没有动作或情绪平静")
        return JSONObject().put("schema_version", InsightPrompt.SCHEMA_VERSION).put("id", UUID.randomUUID().toString())
            .put("type", type).put("source", "collar_ecg_imu").put("synthetic", false)
            .put("observed_at", now.toString()).put("metrics", metrics).put("metric_definitions", definitions)
            .put("context", context).put("evidence", evidence)
    }

    fun exampleEvent(): JSONObject {
        val now = Instant.now()
        val example = CollarSnapshot(acquisition = AcquisitionState.RUNNING,
            heart = HeartSnapshot(bpm = 72.0, signalQuality = .9, observedAt = now),
            imu = ImuSnapshot(motionScore = 6.0, stillRatioPercent = 94.0, online = true, observedAt = now),
            dataAgeMs = 0, hrvReferenceRmssdMs = 42.0, hrvReferenceRrCount = 10, hrvReferenceWindowSeconds = 8.2)
        return checkNotNull(liveEvent(example)).put("source", "synthetic_api_check").put("synthetic", true)
    }

    fun cards(event: JSONObject, explanations: List<AiExplanation>, source: String): List<InsightCard> {
        val evidence = event.getJSONObject("evidence")
        val hasReference = event.getJSONObject("context").getJSONArray("reference_metrics").length() > 0
        val note = when {
            event.getBoolean("synthetic") -> "合成示例 · 用于预览，不代表你的身体状态。"
            hasReference -> "≈ 表示短窗口参考值；卡片解释本次记录。"
            else -> "基于本次身体记录；卡片解释本次记录。"
        }
        val createdAt = Instant.parse(event.getString("observed_at"))
        return explanations.map { item -> InsightCard(
            UUID.randomUUID().toString(), item.title, item.explanation, source, event.getString("id"), createdAt,
            evidence = item.evidenceIds.joinToString("\n") { evidence.getString(it) }, caveat = note,
            suggestion = item.suggestion.takeIf { it.isNotBlank() }, kind = item.kind, schemaVersion = InsightPrompt.SCHEMA_VERSION)
        }
    }

    fun localCards(event: JSONObject): List<InsightCard> {
        val values = event.getJSONObject("metrics")
        val evidence = event.getJSONObject("evidence")
        val available = evidence.keys().asSequence().toList()
        fun ids(vararg preferred: String): List<String> = preferred.filter { evidence.has(it) }.take(3).ifEmpty { available.take(1) }
        val rhythmIds = ids("heart_rate_bpm", "hrv_rmssd_ms")
        val activityIds = ids("motion_score", "still_ratio_percent")
        val bpm = values.optDouble("heart_rate_bpm", Double.NaN).takeIf { it.isFinite() }
        val hrv = values.optDouble("hrv_rmssd_ms", Double.NaN).takeIf { it.isFinite() }
        val rhythm = when {
            bpm != null && hrv != null -> "当前心率约 ${number(bpm)} BPM，RMSSD 约 ${number(hrv)} ms；心率描述心搏频率，RMSSD 描述相邻心搏间期变化，属于两个维度。"
            bpm != null -> "当前心率约 ${number(bpm)} BPM；这次记录只描述当前心搏频率，不单凭一个数值推断原因。"
            hrv != null -> "当前 RMSSD 约 ${number(hrv)} ms；它描述相邻心搏间期变化，不等同于平均心率。"
            else -> "这次记录包含活动指标；先把它与后续心率变化放在同一时间窗口阅读。"
        }
        val moving = values.has("motion_score") && values.getDouble("motion_score") >= 35
        val quiet = values.has("motion_score") && values.getDouble("motion_score") < 10
        val activityTitle = when { moving -> "近期活动幅度较大"; quiet -> "静止不等于完全不动"; else -> "活动指标描述动作幅度" }
        val activityText = when {
            moving -> "活动指数反映近期动作幅度，静止占比反映低活动桶的比例；二者都不能单独说明运动类型或原因。"
            quiet -> "静止占比表示近期低活动桶占比，即使为 100% 仍可能有轻微动作；它不能说明坐姿、睡眠或情绪。"
            else -> "活动指数与静止占比只描述近期动作窗口；把它们和同一窗口的心率一起看，避免单凭一个指标下结论。"
        }
        val suggestionTitle = when { moving -> "活动结束后看一段缓冲"; quiet -> "无需追着数字调整"; else -> "把心率和活动一起看" }
        val suggestionText = when {
            moving -> "本次窗口有较明显动作，因此建议把活动结束后的心率和动作分开观察。"
            quiet -> "活动幅度已经较小，这次更适合按原状态继续记录，不为降低数字刻意改变动作。"
            else -> "当前活动证据有限，结合心率和活动的变化阅读，比单看某一个数字更可靠。"
        }
        val suggestion = when {
            moving -> "结束手头活动后，留一小段自然缓冲，再看下一段记录。"
            quiet -> "继续手头轻量任务，不必为了某个数字刻意休息或运动。"
            else -> "下一次同时查看心率与活动，不单凭一个数字判断状态。"
        }
        return cards(event, listOf(
            AiExplanation("rhythm", "心率与 RMSSD 的含义不同", rhythm, "", rhythmIds),
            AiExplanation("activity", activityTitle, activityText, "", activityIds),
            AiExplanation("suggestion", suggestionTitle, suggestionText, suggestion, ids("heart_rate_bpm", "motion_score", "still_ratio_percent"))
        ), "本地解读")
    }

    private fun number(value: Double) = String.format(Locale.ROOT, "%.1f", value)
}
