package com.smartneckband.companion.data

import android.content.SharedPreferences
import android.os.SystemClock
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant

data class AiRequestState(
    val busy: Boolean = false,
    val message: String = "配置 API 后，可手动生成解释；自动生成需单独开启。",
    val isError: Boolean = false,
    val preview: List<InsightCard> = emptyList(),
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
                    o.optionalString("evidence"), o.optionalString("caveat"), o.optionalString("suggestion"),
                    o.optString("kind", "rhythm"), o.optInt("schemaVersion", 1))
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
        val event = InsightContent.liveEvent(repository.snapshot.value)
        if (event == null) { feedback("下一段身体记录到来后，就可以开始解读。"); return@launch }
        execute(event, example = false)
    } }

    fun checkWithExample() { scope.launch {
        if (configuring || active?.isActive == true) return@launch
        if (demoEnabled()) { feedback("请先关闭两个演示模式，再使用示例检查 API"); return@launch }
        execute(InsightContent.exampleEvent(), example = true)
    } }

    suspend fun observe(snapshot: CollarSnapshot) = withContext(Dispatchers.Main.immediate) {
        if (configuring || demoEnabled() || active?.isActive == true) return@withContext
        val event = InsightContent.liveEvent(snapshot) ?: return@withContext
        val type = event.getString("type")
        // References and ordinary snapshots can produce everyday observations too.
        val now = SystemClock.elapsedRealtime()
        val elapsed = lastEventMs?.let { now - it }
        if (elapsed != null && (elapsed < 60_000 || type == lastType && elapsed < 300_000)) return@withContext
        if (lastRequestMs?.let { now - it < 60_000 } == true) return@withContext
        lastEventMs = now
        lastType = type
        val config = settings.state.value
        if (config.enabled && config.automatic && !automaticPaused) execute(event, example = false)
        else addCards(InsightContent.localCards(event))
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
            val cards = InsightContent.cards(event, response,
                "${config.provider.label} · ${config.model}" + if (example) " · API 合成示例" else "")
            if (!example) addCards(cards)
            pauseAutomatic(false)
            mutable.value = AiRequestState(message = if (example) "API 调用成功，三张示例卡片如下。" else
                "三张解读已更新 · ${java.time.LocalTime.now().format(java.time.format.DateTimeFormatter.ofPattern("HH:mm"))}",
                preview = if (example) cards else emptyList())
        } catch (e: CancellationException) {
            if (active === job) mutable.value = AiRequestState(message = "请求已取消")
            throw e
        } catch (e: Exception) {
            pauseAutomatic(true)
            if (!example) addCards(InsightContent.localCards(event))
            mutable.value = AiRequestState(message = ((e as? AiFailure)?.message ?: "AI 请求失败，请检查配置后手动重试") +
                if (example) "。未保存示例卡片。" else "。已保留本地说明；自动 AI 已暂停，可手动重试。", isError = true)
        } finally {
            if (active === job) {
                active = null
                mutable.value = mutable.value.copy(busy = false)
            }
        }
    }

    private fun addCards(cards: List<InsightCard>) {
        repository.addInsights(cards)
        val saved = JSONArray()
        repository.insights.value.forEach { value -> saved.put(JSONObject().put("id", value.id).put("title", value.title)
            .put("explanation", value.explanation).put("source", value.source).put("eventId", value.eventId)
            .put("createdAt", value.createdAt.toString()).put("evidence", value.evidence)
            .put("caveat", value.caveat).put("suggestion", value.suggestion)
            .put("kind", value.kind).put("schemaVersion", value.schemaVersion)) }
        preferences.edit().putString("insights", saved.toString()).apply()
    }

    private fun demoEnabled() = preferences.getBoolean("demo_insight", false) || preferences.getBoolean("demo_ecg", false)
    private fun pauseAutomatic(paused: Boolean) {
        automaticPaused = paused
        preferences.edit().putBoolean("ai_automatic_paused", paused).apply()
    }
    private fun feedback(text: String) { mutable.value = AiRequestState(message = text, isError = true) }
    private fun JSONObject.optionalString(key: String) = if (isNull(key)) null else optString(key).takeIf { it.isNotBlank() }

}
