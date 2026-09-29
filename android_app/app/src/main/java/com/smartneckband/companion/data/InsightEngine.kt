package com.smartneckband.companion.data

import android.content.SharedPreferences
import android.os.SystemClock
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.util.Locale
import java.util.UUID

data class AiRequestState(
    val busy: Boolean = false,
    val message: String = "配置 API 后，可手动生成解释；自动生成需单独开启。",
    val isError: Boolean = false,
    val preview: InsightCard? = null,
)

/** One application-owned engine. All request scheduling/card mutation is confined to Main. */
class InsightEngine(
    private val preferences: SharedPreferences,
    private val repository: CollarRepository,
    val settings: AiSettingsStore,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val client = ChatCompletionsClient()
    private val mutable = MutableStateFlow(if (preferences.getBoolean("ai_automatic_paused", false))
        AiRequestState(message = "上次 AI 调用失败，自动生成已暂停；可手动重试或重新保存配置。", isError = true)
        else AiRequestState())
    val state = mutable.asStateFlow()
    private var active: Job? = null
    private var lastEventMs: Long? = null
    private var lastType: String? = null
    private var lastRequestMs: Long? = null
    private var automaticPaused = preferences.getBoolean("ai_automatic_paused", false)
    private var configuring = false

    init {
        val cards = runCatching { JSONArray(preferences.getString("insights", "[]")) }.getOrDefault(JSONArray())
        repository.setInsights((0 until minOf(30, cards.length())).mapNotNull { index ->
            runCatching {
                val o = cards.getJSONObject(index)
                InsightCard(o.getString("id"), o.getString("title"), o.getString("explanation"),
                    o.getString("source"), o.optionalString("eventId"), Instant.parse(o.getString("createdAt")),
                    o.optionalString("evidence"), o.optionalString("caveat"), o.optionalString("suggestion"))
            }.getOrNull()
        })
    }

    suspend fun saveConfiguration(config: AiConfiguration, newKey: String) = withContext(Dispatchers.Main.immediate) {
        configuring = true
        cancelActive("正在保存配置…")
        try {
            withContext(Dispatchers.IO) { settings.save(config, newKey) }
            pauseAutomatic(false)
            lastEventMs = null
            mutable.value = AiRequestState(message = if (settings.state.value.enabled)
                "配置已保存，可前往 AI Insight 生成解释。" else "配置已保存，真实数据的 AI 请求尚未启用。")
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) {
            mutable.value = AiRequestState(message = (e as? AiFailure)?.message ?: "配置保存失败，请重新填写密钥后重试", isError = true)
            throw AiFailure(mutable.value.message)
        } finally { configuring = false }
    }

    suspend fun clearKey() = withContext(Dispatchers.Main.immediate) {
        configuring = true
        cancelActive("正在删除密钥…")
        try {
            withContext(Dispatchers.IO) { settings.clearKey() }
            pauseAutomatic(false)
            mutable.value = AiRequestState(message = "密钥已删除，AI 请求已关闭。")
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) {
            mutable.value = AiRequestState(message = (e as? AiFailure)?.message ?: "删除失败，请重试", isError = true)
        } finally { configuring = false }
    }

    fun cancelActive(message: String = "请求已取消") {
        active?.cancel()
        active = null
        mutable.value = AiRequestState(message = message)
    }

    fun generateNow() { scope.launch {
        if (configuring || active?.isActive == true) return@launch
        if (demoEnabled()) { feedback("请先在 Settings 关闭两个演示模式"); return@launch }
        if (!settings.state.value.enabled) { feedback("请先在 Settings 配置并启用 AI Insight"); return@launch }
        val event = liveEvent(repository.snapshot.value)
        if (event == null) { feedback("尚无新鲜的采集数据，请开始采集后重试"); return@launch }
        execute(event, example = false)
    } }

    fun checkWithExample() { scope.launch {
        if (configuring || active?.isActive == true) return@launch
        if (demoEnabled()) { feedback("请先关闭两个演示模式，再使用示例检查 API"); return@launch }
        val example = JSONObject().put("schema_version", 1).put("id", UUID.randomUUID().toString())
            .put("type", "body.observation").put("source", "synthetic_api_check").put("synthetic", true)
            .put("observed_at", Instant.now().toString())
            .put("metrics", JSONObject().put("heart_rate_bpm", 72).put("hrv_rmssd_ms", JSONObject.NULL))
            .put("quality", JSONObject().put("signal_quality", .9).put("timing_warning", false).put("lead_off", false))
            .put("limitations", JSONArray().put("纯合成示例，不能描述用户的真实身体状态").put("HRV 静息窗口尚未完成"))
            .put("evidence", JSONObject().put("heart_rate_bpm", "合成心率 72 BPM")
                .put("hrv_status", "合成场景：HRV 尚未积累完成，不提供 RMSSD"))
        execute(example, example = true)
    } }

    suspend fun observe(snapshot: CollarSnapshot) = withContext(Dispatchers.Main.immediate) {
        if (configuring || demoEnabled() || active?.isActive == true) return@withContext
        val event = liveEvent(snapshot) ?: return@withContext
        val type = event.getString("type")
        // Automatic physiological explanations retain the existing quality/rest/movement gates.
        if (type !in setOf("body.movement", "body.rest_window")) return@withContext
        val now = SystemClock.elapsedRealtime()
        val elapsed = lastEventMs?.let { now - it }
        if (elapsed != null && (elapsed < 60_000 || type == lastType && elapsed < 300_000)) return@withContext
        if (lastRequestMs?.let { now - it < 60_000 } == true) return@withContext
        lastEventMs = now
        lastType = type
        val config = settings.state.value
        if (config.enabled && config.automatic && !automaticPaused) execute(event, example = false)
        else addCard(localCard(event, if (automaticPaused) "本地规则 · 自动 AI 已暂停" else "本地规则"))
    }

    private suspend fun execute(event: JSONObject, example: Boolean) {
        val now = SystemClock.elapsedRealtime()
        if (lastRequestMs?.let { now - it < 10_000 } == true) {
            feedback("请求过于频繁，请等待 10 秒后再试")
            return
        }
        val config = settings.state.value
        if (!config.hasKey) { feedback("请先在 Settings 保存 API Key"); return }
        val job = currentCoroutineContext().job
        active = job
        lastRequestMs = now
        lastEventMs = now
        lastType = event.getString("type")
        mutable.value = AiRequestState(busy = true, message = if (example) "正在用合成示例检查 API…" else "正在解释本次记录…")
        try {
            val apiKey = withContext(Dispatchers.IO) { settings.readKey(config) }
            currentCoroutineContext().ensureActive()
            val response = client.explain(config, apiKey, event)
            currentCoroutineContext().ensureActive()
            if (settings.state.value.revision != config.revision || demoEnabled()) return
            val evidence = event.getJSONObject("evidence")
            val card = InsightCard(UUID.randomUUID().toString(), response.title, response.explanation,
                "${config.provider.label} · ${config.model}" + if (example) " · API 合成示例" else "",
                event.getString("id"), evidence = response.evidenceIds.joinToString("\n") { evidence.getString(it) },
                caveat = if (example) "API 连接检查使用合成数据，不代表你的身体状态，不保存到真实记录。" else CAVEAT,
                suggestion = response.suggestion)
            if (!example) addCard(card)
            pauseAutomatic(false)
            mutable.value = AiRequestState(message = if (example) "API 调用成功，示例卡片如下。" else
                "已生成解释 · ${java.time.LocalTime.now().format(java.time.format.DateTimeFormatter.ofPattern("HH:mm"))}",
                preview = if (example) card else null)
        } catch (e: CancellationException) {
            if (active === job) mutable.value = AiRequestState(message = "请求已取消")
            throw e
        } catch (e: Exception) {
            pauseAutomatic(true)
            if (!example) addCard(localCard(event, "本地规则 · AI 暂不可用"))
            mutable.value = AiRequestState(message = ((e as? AiFailure)?.message ?: "AI 请求失败，请检查配置后手动重试") +
                if (example) "。未保存示例卡片。" else "。已保留本地说明；自动 AI 已暂停，可手动重试。", isError = true)
        } finally {
            if (active === job) {
                active = null
                mutable.value = mutable.value.copy(busy = false)
            }
        }
    }

    private fun liveEvent(snapshot: CollarSnapshot): JSONObject? {
        if (snapshot.acquisition != AcquisitionState.RUNNING || snapshot.dataAgeMs?.let { it in 0..3000 } != true) return null
        val now = Instant.now()
        fun fresh(at: Instant?) = at?.let { now.toEpochMilli() - it.toEpochMilli() in 0..5000 } == true
        val quality = snapshot.heart.signalQuality?.takeIf { it.isFinite() && it in 0.0..1.0 && fresh(snapshot.heart.observedAt) }
        val trusted = quality != null && quality >= .5 && !snapshot.heart.leadOff && !snapshot.timingWarning
        val bpm = snapshot.heart.bpm?.takeIf { trusted && it.isFinite() && it in 30.0..240.0 }
        val motion = snapshot.imu.motionScore?.takeIf { snapshot.imu.online && fresh(snapshot.imu.observedAt) && it.isFinite() && it >= 0 }
        val hrv = snapshot.heart.hrvRmssdMs?.takeIf { bpm != null && snapshot.hrvStatus == "ready" &&
            snapshot.hrvWindowSeconds >= 60 && snapshot.rrCount >= 30 && motion != null && motion < 10 && it.isFinite() && it >= 0 }
        val type = when {
            bpm == null -> "body.signal_quality"
            motion != null && motion >= 35 -> "body.movement"
            hrv != null -> "body.rest_window"
            else -> "body.observation"
        }
        val hrvText = if (hrv != null) "有效 RMSSD ${number(hrv)} ms；静息窗口 ${snapshot.hrvWindowSeconds} 秒，有效 RR ${snapshot.rrCount} 个"
            else "HRV 暂不可用：${snapshot.hrvReasons.ifEmpty { listOf("当前数据未通过完整质量检查") }.joinToString("；")}"
        val evidence = JSONObject().put("hrv_status", hrvText)
            .put("signal_status", "${if (snapshot.heart.leadOff) "电极脱落" else "未报告电极脱落"}；${if (snapshot.timingWarning) "采样时序告警" else "未报告采样时序告警"}")
        quality?.let { evidence.put("signal_quality", "信号质量 ${number(it * 100)}%") }
        bpm?.let { evidence.put("heart_rate_bpm", "本次心率 ${number(it)} BPM") }
        hrv?.let { evidence.put("hrv_rmssd_ms", "本次 RMSSD ${number(it)} ms") }
        if (bpm != null) motion?.let { evidence.put("motion_score", "本次活动指数 ${number(it)}") }
        val limitations = JSONArray()
        if (hrv == null) limitations.put(hrvText)
        if (bpm == null) limitations.put("当前心率未通过本次解释所需的质量检查；只解释数据质量")
        if (motion == null) limitations.put("暂无新鲜的 IMU 数据，不能推断活动状态")
        limitations.put("没有提供历史基线，不能推断趋势；不能换算压力或作医疗诊断")
        return JSONObject().put("schema_version", 1).put("id", UUID.randomUUID().toString())
            .put("type", type).put("source", "collar_ecg_imu").put("synthetic", false)
            .put("observed_at", now.toString())
            .put("metrics", JSONObject().put("heart_rate_bpm", bpm ?: JSONObject.NULL)
                .put("hrv_rmssd_ms", hrv ?: JSONObject.NULL).put("motion_score", if (bpm != null) motion ?: JSONObject.NULL else JSONObject.NULL))
            .put("quality", JSONObject().put("signal_quality", quality ?: JSONObject.NULL)
                .put("adc_clipped_samples", snapshot.adcClippedSamples).put("analysis_sample_count", snapshot.analysisSampleCount)
                .put("lead_off", snapshot.heart.leadOff).put("timing_warning", snapshot.timingWarning)
                .put("data_age_ms", snapshot.dataAgeMs).put("hrv_status", if (hrv != null) "ready" else "unavailable")
                .put("hrv_window_s", snapshot.hrvWindowSeconds).put("rr_count", snapshot.rrCount))
            .put("limitations", limitations).put("evidence", evidence)
    }

    private fun localCard(event: JSONObject, source: String): InsightCard {
        val (title, text) = when (event.getString("type")) {
            "body.movement" -> "检测到身体活动" to "这段时间 IMU 检测到明显活动。活动会影响心率与 HRV，建议在稳定静息时比较记录。"
            "body.rest_window" -> "一段稳定的静息记录" to "已积累有效静息数据，可查看本次 RMSSD。单次 HRV 不直接代表压力水平。"
            "body.observation" -> "本次身体记录" to "已收到可用的心率记录。HRV 与活动状态以本次数据是否就绪为准，暂不推断长期变化。"
            else -> "本次信号仍需改善" to "当前数据未通过身体状态解释所需的质量检查。请查看测量依据中的采样与电极状态。"
        }
        val evidence = event.getJSONObject("evidence")
        return InsightCard(UUID.randomUUID().toString(), title, text, source, event.getString("id"),
            evidence = evidence.keys().asSequence().map { evidence.getString(it) }.joinToString("\n"), caveat = CAVEAT)
    }

    private fun addCard(card: InsightCard) {
        repository.addInsight(card)
        val saved = JSONArray()
        repository.insights.value.forEach { value -> saved.put(JSONObject().put("id", value.id).put("title", value.title)
            .put("explanation", value.explanation).put("source", value.source).put("eventId", value.eventId)
            .put("createdAt", value.createdAt.toString()).put("evidence", value.evidence)
            .put("caveat", value.caveat).put("suggestion", value.suggestion)) }
        preferences.edit().putString("insights", saved.toString()).apply()
    }

    private fun demoEnabled() = preferences.getBoolean("demo_insight", false) || preferences.getBoolean("demo_ecg", false)
    private fun pauseAutomatic(paused: Boolean) {
        automaticPaused = paused
        preferences.edit().putBoolean("ai_automatic_paused", paused).apply()
    }
    private fun feedback(text: String) { mutable.value = AiRequestState(message = text, isError = true) }
    private fun number(value: Double) = String.format(Locale.ROOT, "%.1f", value)
    private fun JSONObject.optionalString(key: String) = if (isNull(key)) null else optString(key).takeIf { it.isNotBlank() }

    private companion object { const val CAVEAT = "AI 解释仅基于本次记录，可能有误；不用于诊断，也不将 HRV 换算为压力分数。" }
}
