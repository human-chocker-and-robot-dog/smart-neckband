package com.smartneckband.companion.data

import android.content.SharedPreferences
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.URL
import javax.net.ssl.HttpsURLConnection
import java.time.Instant
import java.util.UUID

/** Sends bounded structured events only. Local cards are explicitly labelled. */
class InsightEngine(private val preferences: SharedPreferences, private val repository: CollarRepository) {
    private var lastEventMs = 0L
    private var lastType: String? = null

    init {
        runCatching {
            val array = JSONArray(preferences.getString("insights", "[]"))
            repository.setInsights((0 until array.length()).map { index ->
                val o = array.getJSONObject(index)
                InsightCard(o.getString("id"), o.getString("title"), o.getString("explanation"),
                    o.getString("source"), o.optString("eventId"), Instant.parse(o.getString("createdAt")))
            })
        }
    }

    suspend fun observe(snapshot: CollarSnapshot) {
        val now = System.currentTimeMillis()
        if (snapshot.acquisition != AcquisitionState.RUNNING || snapshot.dataAgeMs == null || snapshot.dataAgeMs > 3000) return
        val quality = snapshot.heart.signalQuality ?: return
        val bpm = snapshot.heart.bpm ?: return
        val motion = snapshot.imu.motionScore ?: return
        if (quality < .5 || snapshot.heart.leadOff) return
        val type = if (motion >= 35) "body.movement" else if (snapshot.heart.hrvRmssdMs != null) "body.rest_window" else return
        if (now - lastEventMs < 60_000 || (type == lastType && now - lastEventMs < 300_000)) return
        lastEventMs = now
        lastType = type
        val event = JSONObject().put("schema_version", 1).put("id", UUID.randomUUID().toString())
            .put("type", type).put("observed_at", Instant.now().toString())
            .put("source", "collar_ecg_imu").put("heart_rate_bpm", bpm)
            .put("hrv_rmssd_ms", snapshot.heart.hrvRmssdMs ?: JSONObject.NULL)
            .put("signal_quality", quality).put("motion_score", motion)
            .put("hrv_window_s", snapshot.hrvWindowSeconds).put("rr_count", snapshot.rrCount)
            .put("lead_off", false).put("data_age_ms", snapshot.dataAgeMs)
        val localTitle = if (type == "body.movement") "检测到身体活动" else "一段稳定的静息记录"
        val localText = if (type == "body.movement")
            "这段时间 IMU 检测到明显活动。活动会影响心率与 HRV，建议在稳定静息时比较趋势。"
        else "已积累 60 秒稳定静息数据，可查看本次 RMSSD。单次 HRV 不直接代表压力水平，后续可与个人长期基线比较。"
        var title = localTitle
        var explanation = localText
        var source = "本地规则"
        val endpoint = preferences.getString("insight_endpoint", "").orEmpty()
        if (preferences.getBoolean("cloud_insight", false) && endpoint.startsWith("https://")) {
            val remote = runCatching { request(endpoint, event) }.getOrNull()
            if (remote != null) {
                title = remote.first
                explanation = remote.second
                source = "AI Insight"
            } else source = "本地规则 · AI 暂不可用"
        }
        repository.addInsight(InsightCard(UUID.randomUUID().toString(), title, explanation, source, event.getString("id")))
        val saved = JSONArray()
        repository.insights.value.forEach { card -> saved.put(JSONObject().put("id", card.id).put("title", card.title)
            .put("explanation", card.explanation).put("source", card.source).put("eventId", card.eventId)
            .put("createdAt", card.createdAt.toString())) }
        preferences.edit().putString("insights", saved.toString()).apply()
    }

    private suspend fun request(endpoint: String, event: JSONObject): Pair<String, String> = withContext(Dispatchers.IO) {
        val url = URL(endpoint)
        require(url.protocol == "https" && url.userInfo == null) { "需要 HTTPS 网关" }
        val connection = url.openConnection() as HttpsURLConnection
        try {
            connection.instanceFollowRedirects = false
            connection.connectTimeout = 10_000
            connection.readTimeout = 15_000
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            val body = JSONObject().put("event", event).put("locale", "zh-CN")
            connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            check(connection.responseCode in 200..299)
            val bytes = connection.inputStream.use { input ->
                val bounded = ByteArray(16_385)
                var count = 0
                while (count < bounded.size) {
                    val read = input.read(bounded, count, bounded.size - count)
                    if (read < 0) break
                    count += read
                }
                bounded.copyOf(count)
            }
            require(bytes.size <= 16_384)
            val response = JSONObject(bytes.toString(Charsets.UTF_8))
            val title = response.getString("title")
            val text = response.getString("explanation")
            require(title.length in 1..100 && text.length in 1..2000)
            title to text
        } finally { connection.disconnect() }
    }
}
