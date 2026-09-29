package com.smartneckband.companion.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.smartneckband.companion.SmartCollarApplication
import com.smartneckband.companion.data.*
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

@Composable
fun AiSettingsCard(app: SmartCollarApplication, scope: CoroutineScope) {
    val engine = app.insightEngine
    val saved by app.aiSettings.state.collectAsStateWithLifecycle()
    val request by engine.state.collectAsStateWithLifecycle()
    val ecgDemo by app.ecgDemo.collectAsStateWithLifecycle()
    val insightDemo by app.insightDemo.collectAsStateWithLifecycle()
    var draft by remember(saved.revision) { mutableStateOf(saved) }
    // Never use rememberSaveable or refill a decrypted key into the editor.
    var key by remember(saved.revision) { mutableStateOf("") }
    var saving by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val dirty = draft != saved || key.isNotEmpty()
    Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(24.dp)) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("AI API 接入", style = MaterialTheme.typography.titleLarge)
            Text("手机直接请求你选择的服务。只发送本次指标摘要和质量状态，不上传原始 ECG、音频或设备标识。")
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AiProvider.entries.forEach { provider -> FilterChip(
                    selected = draft.provider == provider,
                    enabled = !saving,
                    onClick = {
                        if (provider != draft.provider) {
                            draft = draft.copy(provider = provider,
                                baseUrl = if (provider == AiProvider.DEEPSEEK) "https://api.deepseek.com" else "https://api.openai.com/v1",
                                model = if (provider == AiProvider.DEEPSEEK) "deepseek-flash" else "",
                                jsonMode = true, modernTokenLimit = provider == AiProvider.COMPATIBLE)
                            key = ""
                        }
                    }, label = { Text(provider.label) }) }
            }
            OutlinedTextField(draft.baseUrl, { draft = draft.copy(baseUrl = it) },
                modifier = Modifier.fillMaxWidth(), label = { Text("API 基础地址") }, singleLine = true,
                enabled = !saving && draft.provider == AiProvider.COMPATIBLE,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                supportingText = { Text("支持基础地址、/v1，或完整 /chat/completions 地址。") })
            OutlinedTextField(draft.model, { draft = draft.copy(model = it) },
                modifier = Modifier.fillMaxWidth(), label = { Text("模型 ID") }, singleLine = true, enabled = !saving)
            OutlinedTextField(key, { key = it }, modifier = Modifier.fillMaxWidth(), label = { Text("API Key") },
                placeholder = { Text(if (saved.hasKey) "已加密保存；留空保留" else "在手机上粘贴密钥") },
                singleLine = true, enabled = !saving, visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                supportingText = { Text("使用 Android Keystore 加密保存在本机；更换 API 地址需重新填写。") })
            SettingSwitch("启用 AI Insight", "保存后允许发送真实指标摘要，调用消耗你账户的 API 额度。",
                draft.enabled, !saving) { draft = draft.copy(enabled = it) }
            SettingSwitch("自动生成解释", "有效活动或静息事件触发；至少间隔 1 分钟，同类事件至少 5 分钟。",
                draft.automatic, !saving && draft.enabled) { draft = draft.copy(automatic = it) }
            if (draft.provider == AiProvider.COMPATIBLE) {
                SettingSwitch("JSON 模式", "默认开启；仅当服务不支持 response_format 时关闭，返回内容仍会校验。",
                    draft.jsonMode, !saving) { draft = draft.copy(jsonMode = it) }
                SettingSwitch("使用 max_completion_tokens", "OpenAI 新模型使用此参数；旧兼容服务关闭后使用 max_tokens。",
                    draft.modernTokenLimit, !saving) { draft = draft.copy(modernTokenLimit = it) }
            }
            if (dirty) Text("有未保存的修改；请求使用上一次保存的配置。", style = MaterialTheme.typography.bodySmall)
            error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            Button(onClick = {
                saving = true
                error = null
                val submitted = draft
                val suppliedKey = key
                scope.launch {
                    try {
                        engine.saveConfiguration(submitted, suppliedKey)
                        key = ""
                    } catch (e: CancellationException) { throw e }
                    catch (e: AiFailure) { error = e.message }
                    finally { saving = false }
                }
            }, enabled = !saving, modifier = Modifier.fillMaxWidth()) { Text(if (saving) "正在保存…" else "保存 AI 配置") }
            OutlinedButton(onClick = engine::checkWithExample,
                enabled = saved.hasKey && !saving && !dirty && !request.busy && !ecgDemo && !insightDemo,
                modifier = Modifier.fillMaxWidth()) { Text("用示例检查 API") }
            Text("此按钮会实际调用已保存的 API，使用合成数据并消耗额度；结果只显示在这里，不加入真实记录。", style = MaterialTheme.typography.bodySmall)
            if (ecgDemo || insightDemo) Text("演示模式开启时暂停全部 AI 请求；关闭两个演示开关后可检查 API。")
            AiRequestStatus(request) { engine.cancelActive() }
            request.preview?.let { card ->
                HorizontalDivider()
                Text("API 示例 · ${card.title}", style = MaterialTheme.typography.titleMedium)
                Text(card.explanation)
                card.evidence?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
                card.suggestion?.let { Text("可以试试：$it") }
                card.caveat?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
                Text(card.source, style = MaterialTheme.typography.labelSmall)
            }
            if (saved.hasKey) TextButton(onClick = {
                saving = true
                key = ""
                scope.launch { try { engine.clearKey() } finally { saving = false } }
            }, enabled = !saving) { Text("删除本机 API Key 并关闭 AI") }
        }
    }
}

@Composable
fun AiRequestStatus(state: AiRequestState, cancel: () -> Unit) {
    if (state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
    Text(state.message, color = if (state.isError) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
        style = MaterialTheme.typography.bodyMedium)
    if (state.busy) TextButton(onClick = cancel) { Text("取消请求") }
}

@Composable
private fun SettingSwitch(title: String, description: String, value: Boolean, enabled: Boolean, change: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleSmall)
            Text(description, style = MaterialTheme.typography.bodySmall)
        }
        Switch(checked = value, onCheckedChange = change, enabled = enabled)
    }
}
