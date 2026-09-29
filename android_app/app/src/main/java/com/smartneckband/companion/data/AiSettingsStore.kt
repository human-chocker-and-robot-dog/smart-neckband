package com.smartneckband.companion.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.net.URI
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

enum class AiProvider(val label: String) {
    DEEPSEEK("DeepSeek 官方"), COMPATIBLE("OpenAI 兼容 API")
}

data class AiConfiguration(
    val provider: AiProvider = AiProvider.DEEPSEEK,
    val baseUrl: String = "https://api.deepseek.com",
    val model: String = "deepseek-flash",
    val enabled: Boolean = false,
    val automatic: Boolean = false,
    val jsonMode: Boolean = true,
    val modernTokenLimit: Boolean = false,
    val hasKey: Boolean = false,
    val revision: Long = 0,
) {
    fun endpoint(): String {
        val uri = try { URI(baseUrl.trim().trimEnd('/')) } catch (_: Exception) {
            throw AiFailure("API 地址格式不正确")
        }
        if (!uri.scheme.equals("https", true) || uri.host.isNullOrBlank() ||
            uri.rawUserInfo != null || uri.rawQuery != null || uri.rawFragment != null ||
            uri.port !in -1..65535 || uri.port == 0 || uri.normalize() != uri) {
            throw AiFailure("请填写 HTTPS 基础地址，不要包含密钥、查询参数或片段")
        }
        val path = uri.rawPath.orEmpty().trimEnd('/')
        val endpointPath = if (path.endsWith("/chat/completions")) path else "$path/chat/completions"
        val port = if (uri.port == -1 || uri.port == 443) "" else ":${uri.port}"
        val endpoint = "https://${uri.host.lowercase()}$port$endpointPath"
        if (provider == AiProvider.DEEPSEEK && endpoint !in setOf(
                "https://api.deepseek.com/chat/completions", "https://api.deepseek.com/v1/chat/completions")) {
            throw AiFailure("DeepSeek 官方模式仅接受 api.deepseek.com；其他服务请选择兼容 API")
        }
        return endpoint
    }

    fun validate() {
        endpoint()
        if (model.length !in 1..128 || model.any { it.code !in 33..126 }) {
            throw AiFailure("请填写服务商提供的模型 ID（1–128 个英文字符）")
        }
    }
}

/** Only encrypted credential bytes are persisted; endpoint is authenticated as AES-GCM AAD. */
class AiSettingsStore(context: Context) {
    private val preferences = context.getSharedPreferences("ai_provider", Context.MODE_PRIVATE)
    private val mutable = MutableStateFlow(load())
    val state = mutable.asStateFlow()

    private fun load() = AiConfiguration(
        provider = runCatching { AiProvider.valueOf(preferences.getString("provider", "DEEPSEEK")!!) }
            .getOrDefault(AiProvider.DEEPSEEK),
        baseUrl = preferences.getString("base_url", "https://api.deepseek.com").orEmpty(),
        model = preferences.getString("model", "deepseek-flash").orEmpty(),
        enabled = preferences.getBoolean("enabled", false),
        automatic = preferences.getBoolean("automatic", false),
        jsonMode = preferences.getBoolean("json_mode", true),
        modernTokenLimit = preferences.getBoolean("modern_token_limit", false),
        hasKey = preferences.contains("key_ciphertext") && preferences.contains("key_iv"),
        revision = preferences.getLong("revision", 0),
    )

    @Synchronized
    fun save(draft: AiConfiguration, newKey: String): AiConfiguration {
        val config = draft.copy(baseUrl = draft.baseUrl.trim().trimEnd('/'), model = draft.model.trim())
        config.validate()
        val endpoint = config.endpoint()
        val old = mutable.value
        val supplied = newKey.trim()
        if (supplied.isNotEmpty() && (supplied.length !in 8..4096 || supplied.any { it.code !in 33..126 })) {
            throw AiFailure("API Key 格式不正确，请只粘贴密钥本身")
        }
        val keepKey = old.hasKey && preferences.getString("key_endpoint", null) == endpoint
        if (supplied.isEmpty() && old.hasKey && !keepKey) {
            throw AiFailure("API 地址已改变，请为新地址重新填写密钥")
        }
        if (config.enabled && supplied.isEmpty() && !keepKey) throw AiFailure("启用 AI 前请填写 API Key")
        val edit = preferences.edit()
        if (supplied.isNotEmpty()) {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, encryptionKey(create = true))
            cipher.updateAAD(endpoint.toByteArray(Charsets.UTF_8))
            edit.putString("key_iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
                .putString("key_ciphertext", Base64.encodeToString(cipher.doFinal(supplied.toByteArray(Charsets.UTF_8)), Base64.NO_WRAP))
                .putString("key_endpoint", endpoint)
        }
        val saved = config.copy(hasKey = supplied.isNotEmpty() || keepKey, revision = old.revision + 1)
        if (!edit.putString("provider", saved.provider.name).putString("base_url", saved.baseUrl)
                .putString("model", saved.model).putBoolean("enabled", saved.enabled)
                .putBoolean("automatic", saved.automatic).putBoolean("json_mode", saved.jsonMode)
                .putBoolean("modern_token_limit", saved.modernTokenLimit)
                .putLong("revision", saved.revision).commit()) throw AiFailure("配置保存失败，请重试")
        mutable.value = saved
        return saved
    }

    @Synchronized
    fun clearKey() {
        val old = mutable.value
        if (!preferences.edit().remove("key_iv").remove("key_ciphertext").remove("key_endpoint")
                .putBoolean("enabled", false).putLong("revision", old.revision + 1).commit()) {
            throw AiFailure("密钥删除失败，请重试")
        }
        mutable.value = old.copy(hasKey = false, enabled = false, revision = old.revision + 1)
    }

    @Synchronized
    fun readKey(config: AiConfiguration): String {
        if (mutable.value.revision != config.revision) throw AiFailure("配置已更新，请重新生成")
        if (!config.hasKey || preferences.getString("key_endpoint", null) != config.endpoint()) {
            throw AiFailure("请在 Settings 保存当前服务的 API Key")
        }
        return try {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            val iv = Base64.decode(preferences.getString("key_iv", null), Base64.NO_WRAP)
            cipher.init(Cipher.DECRYPT_MODE, encryptionKey(create = false), GCMParameterSpec(128, iv))
            cipher.updateAAD(config.endpoint().toByteArray(Charsets.UTF_8))
            cipher.doFinal(Base64.decode(preferences.getString("key_ciphertext", null), Base64.NO_WRAP))
                .toString(Charsets.UTF_8)
        } catch (_: Exception) { throw AiFailure("本机密钥无法读取，请重新填写并保存 API Key") }
    }

    private fun encryptionKey(create: Boolean): SecretKey {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        check(create)
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256).build())
        }.generateKey()
    }

    private companion object { const val KEY_ALIAS = "smart_collar_personal_ai_v1" }
}

/** Safe, app-authored messages only. Never construct this exception from a server body or key. */
class AiFailure(message: String) : Exception(message)
