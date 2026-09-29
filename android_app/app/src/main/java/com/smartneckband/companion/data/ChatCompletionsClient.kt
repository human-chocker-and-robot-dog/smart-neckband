package com.smartneckband.companion.data

import kotlinx.coroutines.*
import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener
import java.io.IOException
import java.net.SocketTimeoutException
import java.net.URL
import javax.net.ssl.HttpsURLConnection
import kotlin.coroutines.resumeWithException
import kotlin.coroutines.resume

data class AiExplanation(val kind: String, val title: String, val explanation: String, val suggestion: String, val evidenceIds: List<String>)

/** A personal-key Chat Completions client. No raw response, request or auth logging. */
class ChatCompletionsClient {
    private val networkScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    suspend fun explain(config: AiConfiguration, apiKey: String, event: JSONObject): List<AiExplanation> {
        config.validate()
        val body = JSONObject().put("model", config.model).put("stream", false)
            .put(if (config.modernTokenLimit) "max_completion_tokens" else "max_tokens", 2048)
            .put("messages", JSONArray()
                .put(JSONObject().put("role", "system").put("content", InsightPrompt.system))
                .put(JSONObject().put("role", "user").put("content", event.toString())))
        if (config.jsonMode) body.put("response_format", JSONObject().put("type", "json_object"))
        if (config.provider == AiProvider.DEEPSEEK) body.put("thinking", JSONObject().put("type", "disabled"))
        val raw = try {
            withTimeout(60_000) { post(config.endpoint(), apiKey, body.toString()) }
        } catch (_: TimeoutCancellationException) {
            throw AiFailure("AI 请求超过 60 秒，请稍后手动重试")
        }
        currentCoroutineContext().ensureActive()
        return try { parse(raw, event) } catch (e: AiFailure) { throw e }
        catch (_: Exception) { throw AiFailure("AI 返回的 JSON 不符合卡片格式，请重试") }
    }

    private suspend fun post(endpoint: String, apiKey: String, body: String): String = suspendCancellableCoroutine { continuation ->
        val connection = URL(endpoint).openConnection() as HttpsURLConnection
        val worker = networkScope.launch {
            try {
                connection.instanceFollowRedirects = false
                connection.connectTimeout = 10_000
                connection.readTimeout = 30_000
                connection.requestMethod = "POST"
                connection.doOutput = true
                connection.useCaches = false
                connection.setRequestProperty("Authorization", "Bearer $apiKey")
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                connection.setRequestProperty("Accept", "application/json")
                val bytes = body.toByteArray(Charsets.UTF_8)
                connection.setFixedLengthStreamingMode(bytes.size)
                ensureActive()
                connection.outputStream.use { it.write(bytes) }
                val code = connection.responseCode
                if (code !in 200..299) throw AiFailure(when (code) {
                    400, 422 -> "API 拒绝请求（HTTP $code）：检查模型 ID、JSON 模式和 token 参数"
                    401 -> "密钥无效或已过期（HTTP 401），请重新填写"
                    402 -> "API 账户余额不足（HTTP 402）"
                    403 -> "API 拒绝访问（HTTP 403），请检查账户权限"
                    404 -> "接口或模型不存在（HTTP 404），请检查地址和模型 ID"
                    429 -> "API 限流或额度不足（HTTP 429），请稍后手动重试"
                    in 300..399 -> "API 返回重定向，已停止请求；请填写最终 HTTPS 地址"
                    in 500..599 -> "AI 服务暂不可用（HTTP $code），请稍后手动重试"
                    else -> "AI 请求失败（HTTP $code）"
                })
                val response = connection.inputStream.use { input ->
                    val buffer = ByteArray(65_537)
                    var count = 0
                    while (count < buffer.size) {
                        ensureActive()
                        val read = input.read(buffer, count, buffer.size - count)
                        if (read < 0) break
                        count += read
                    }
                    if (count > 65_536) throw AiFailure("AI 响应过大，已拒绝显示")
                    buffer.copyOf(count).toString(Charsets.UTF_8)
                }
                continuation.resume(response)
            } catch (e: Exception) {
                val safe = when (e) {
                    is CancellationException -> e
                    is AiFailure -> e
                    is SocketTimeoutException -> AiFailure("AI 连接或读取超时，请检查网络后重试")
                    is IOException -> AiFailure("无法连接 AI 服务，请检查手机网络与 HTTPS 地址")
                    else -> AiFailure("AI 请求未完成，请检查配置后重试")
                }
                continuation.resumeWithException(safe)
            } finally { connection.disconnect() }
        }
        continuation.invokeOnCancellation {
            worker.cancel()
            // Disconnect blocking I/O off the UI thread, including timeout/setting/demo changes.
            networkScope.launch { connection.disconnect() }
        }
    }

    private fun parse(raw: String, event: JSONObject): List<AiExplanation> {
        val choice = objectOnly(raw).getJSONArray("choices").getJSONObject(0)
        if (choice.optString("finish_reason") != "stop") throw AiFailure(
            if (choice.optString("finish_reason") == "length") "AI 输出被截断，请使用非推理模型后重试"
            else "AI 未返回完整解释，请稍后重试")
        val message = choice.getJSONObject("message")
        if (!message.isNull("refusal") || message.optJSONArray("tool_calls")?.length()?.let { it > 0 } == true) {
            throw AiFailure("AI 未提供可显示的解释卡片")
        }
        val content = message.opt("content") as? String ?: throw AiFailure("AI 返回了空内容，请重试")
        if (content.isBlank()) throw AiFailure("AI 返回了空内容，请重试")
        if (content.length > 8_192) throw AiFailure("AI 卡片内容过长，已拒绝显示")
        val value = objectOnly(content)
        val fields = setOf("schema_version", "event_id", "cards")
        if (value.keys().asSequence().toSet() != fields || value.opt("schema_version") != InsightPrompt.SCHEMA_VERSION ||
            value.opt("event_id") != event.getString("id")) throw AiFailure("AI 响应与当前事件不匹配，请重试")
        fun text(card: JSONObject, name: String, max: Int, min: Int = 1): String {
            val result = (card.opt(name) as? String)?.trim() ?: throw AiFailure("AI 卡片字段类型不正确")
            if (result.length !in min..max || result.any { it.isISOControl() && it != '\n' && it != '\t' }) {
                throw AiFailure("AI 卡片字段长度或内容格式不正确")
            }
            return result
        }
        val evidence = event.getJSONObject("evidence")
        val cards = value.getJSONArray("cards")
        if (cards.length() != 3) throw AiFailure("AI 未返回完整的三张解读卡片，请重试")
        val kinds = listOf("rhythm", "activity", "suggestion")
        val parsed = (0 until cards.length()).map { index ->
            val card = cards.getJSONObject(index)
            if (card.keys().asSequence().toSet() != setOf("kind", "title", "explanation", "suggestion", "evidence_ids")) {
                throw AiFailure("AI 卡片字段不符合格式，请重试")
            }
            val kind = text(card, "kind", 20)
            if (kind !in kinds) throw AiFailure("AI 卡片主题不正确，请重试")
            val ids = card.getJSONArray("evidence_ids")
            if (ids.length() !in 1..3) throw AiFailure("AI 未引用有效的测量依据")
            val references = (0 until ids.length()).map {
                val id = ids.opt(it) as? String ?: throw AiFailure("AI 依据格式不正确")
                if (!evidence.has(id)) throw AiFailure("AI 引用了不存在的数据，已拒绝显示")
                id
            }
            if (references.distinct().size != references.size) throw AiFailure("AI 重复引用了数据依据")
            val title = text(card, "title", 30)
            val explanation = text(card, "explanation", 240)
            val suggestion = text(card, "suggestion", 100, if (kind == "suggestion") 1 else 0)
            // Diagnostic replies do not become body cards. The caller uses labelled local observations.
            val prose = "$title $explanation $suggestion"
            if (listOf("暂不可用", "无法解读", "无法评估", "数据不足", "等待有效", "电极", "导联", "ADC", "削顶", "采样", "丢包", "信号质量", "硬件", "重新测量")
                    .any { prose.contains(it, ignoreCase = true) }) throw AiFailure("本次回复偏离身体解读主题，请重试")
            AiExplanation(kind, title, explanation, suggestion, references)
        }
        if (parsed.map { it.kind }.toSet() != kinds.toSet()) throw AiFailure("AI 返回了重复的卡片主题，请重试")
        return parsed.sortedBy { kinds.indexOf(it.kind) }
    }

    private fun objectOnly(text: String): JSONObject {
        val parser = JSONTokener(text)
        val value = parser.nextValue()
        if (value !is JSONObject || parser.nextClean() != '\u0000') throw AiFailure("AI 未返回单个 JSON 对象")
        return value
    }

}
