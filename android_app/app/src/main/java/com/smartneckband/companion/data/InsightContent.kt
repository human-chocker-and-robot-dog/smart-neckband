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
        metric("heart_rate_bpm", bpm, "心率", " BPM", snapshot.timingWarning || snapshot.adcClippedSamples > 0)
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
        if (hrv != null) context.put("hrv_window_seconds", if (snapshot.isHrvReference)
            snapshot.hrvReferenceWindowSeconds else snapshot.hrvWindowSeconds)
            .put("hrv_rr_count", if (snapshot.isHrvReference) snapshot.hrvReferenceRrCount else snapshot.rrCount)
        return JSONObject().put("schema_version", InsightPrompt.SCHEMA_VERSION).put("id", UUID.randomUUID().toString())
            .put("type", type).put("source", "collar_ecg_imu").put("synthetic", false)
            .put("observed_at", now.toString()).put("metrics", metrics).put("context", context).put("evidence", evidence)
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
            hasReference -> "≈ 为参考数值 · 结合当下感受阅读，仅作日常观察。"
            else -> "基于本次身体记录 · 仅作日常观察。"
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
        val rhythm = rhythmIds.joinToString("，") { evidence.getString(it) }
        val moving = values.has("motion_score") && values.getDouble("motion_score") >= 35
        val quiet = values.has("motion_score") && values.getDouble("motion_score") < 10
        val activityTitle = when { moving -> "动起来，也留一点余地"; quiet -> "一小段安静的空隙"; else -> "照顾此刻的步调" }
        val activityText = when {
            moving -> "这段记录里身体有一些活动。可以给动作之间留一点缓冲，找到让自己舒适的步调。"
            quiet -> "这段记录里的活动幅度较小。可以利用这点安静，感受肩膀、坐姿和呼吸是否舒适。"
            else -> "把这一刻的身体记录留给自己。忙碌间隙也可以短暂停一停，照顾一下当下的舒适感。"
        }
        return cards(event, listOf(
            AiExplanation("rhythm", "听见此刻的节奏", "$rhythm，为此刻留下了身体的线索。结合现在的感受，看看自己的步调是否舒适。", "", rhythmIds),
            AiExplanation("activity", activityTitle, activityText, "", activityIds),
            AiExplanation("suggestion", "给自己一分钟", "把这次记录当作一个轻柔的提醒，为自己留一点不用赶时间的空隙。",
                if (moving) "在方便的时候放慢脚步，给动作留一点缓冲。" else "放松肩膀，自然呼吸，感受一分钟的当下。", rhythmIds.take(1))
        ), "本地解读")
    }

    private fun number(value: Double) = String.format(Locale.ROOT, "%.1f", value)
}
