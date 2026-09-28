package com.smartneckband.companion.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.smartneckband.companion.NearbyCollar
import com.smartneckband.companion.SmartCollarApplication
import com.smartneckband.companion.data.*
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private val palette = lightColorScheme(primary = Color(0xFF28675C), secondary = Color(0xFF666D51),
    background = Color(0xFFF8FAF6), surface = Color(0xFFF8FAF6), surfaceContainer = Color(0xFFEDF2EB))

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CompanionUi(app: SmartCollarApplication, deviceName: String, nearby: List<NearbyCollar>, scanning: Boolean,
    notice: String?, onScan: () -> Unit, onSelect: (NearbyCollar) -> Unit, onStart: () -> Unit, onStop: () -> Unit) {
    val snapshot by app.repository.snapshot.collectAsStateWithLifecycle()
    val insights by app.repository.insights.collectAsStateWithLifecycle()
    var page by rememberSaveable { mutableStateOf("Today") }
    var detail by rememberSaveable { mutableStateOf(false) }
    MaterialTheme(colorScheme = palette) {
        Scaffold(topBar = { TopAppBar(title = { Text(if (detail) "身体数据详情" else page) },
            navigationIcon = { if (detail) IconButton(onClick = { detail = false }) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, "返回")
            } }) }, bottomBar = {
            NavigationBar {
                listOf("Today" to Icons.Default.FavoriteBorder, "AI Insight" to Icons.Default.AutoAwesome, "Settings" to Icons.Default.Settings).forEach { (name, icon) ->
                    NavigationBarItem(selected = page == name, onClick = { page = name; detail = false },
                        icon = { Icon(icon, null) }, label = { Text(name) })
                }
            }
        }) { padding ->
            LazyColumn(Modifier.fillMaxSize().padding(padding), contentPadding = PaddingValues(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                if (detail) {
                    item { InfoCard("ECG · 原始 ADC", "原始计数，与滤波波形分别保存") { Waveform(snapshot.rawEcg) } }
                    item { InfoCard("ECG · 滤波波形", snapshot.analysisMessage) { Waveform(snapshot.cleanEcg) } }
                    item { InfoCard("HRV · RMSSD", "静息窗口 ${snapshot.hrvWindowSeconds}/60 秒 · 有效 RR ${snapshot.rrCount} 个") {
                        Text("${metric(snapshot.heart.hrvRmssdMs)} ms", style = MaterialTheme.typography.headlineLarge)
                        Text("用于观察个人趋势；本版不将 HRV 换算为压力分数。")
                    } }
                    item { InfoCard("数据质量", "${snapshot.dataAgeMs?.let { "数据延迟 ${it} ms" } ?: "暂无实时数据"}") {
                        Text("信号质量：${snapshot.heart.signalQuality?.let { "%.0f%%".format(it * 100) } ?: "--"}")
                        Text("CRC 错误 ${snapshot.parserCrcErrors} · 丢包 ${snapshot.packetLoss}")
                    } }
                } else when (page) {
                    "Today" -> {
                        item { Text("今天，感受身体的节奏", style = MaterialTheme.typography.headlineSmall) }
                        item { Text(stateLabel(snapshot), color = MaterialTheme.colorScheme.primary) }
                        item { Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            MetricCard("心率", metric(snapshot.heart.bpm, 0), "BPM", Modifier.weight(1f)) { detail = true }
                            MetricCard("HRV", metric(snapshot.heart.hrvRmssdMs), "RMSSD · ms", Modifier.weight(1f)) { detail = true }
                        } }
                        item { Card(onClick = { detail = true }, shape = RoundedCornerShape(24.dp)) {
                            Column(Modifier.fillMaxWidth().padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                                Text("ECG", style = MaterialTheme.typography.titleLarge)
                                Waveform(snapshot.cleanEcg.ifEmpty { snapshot.rawEcg })
                                Text(snapshot.analysisMessage, style = MaterialTheme.typography.bodyMedium)
                                Text("查看原始波形与详情 →", color = MaterialTheme.colorScheme.primary)
                            }
                        } }
                        item { InfoCard("身体活动", motionLabel(snapshot.imu.level)) {
                            Text("活动指数 ${metric(snapshot.imu.motionScore)} · 静止占比 ${metric(snapshot.imu.stillRatioPercent, 0)}%")
                            Text(if (snapshot.imu.online) "IMU 在线" else "等待 IMU 数据", style = MaterialTheme.typography.bodySmall)
                        } }
                        item { Text(snapshot.lastError ?: if (snapshot.connection == ConnectionState.DISCONNECTED) "设备配置和采集控制位于 Settings。" else "数据来自颈环的实时 ECG 与 IMU。", style = MaterialTheme.typography.bodySmall) }
                    }
                    "AI Insight" -> {
                        item { Text("理解每一段身体变化", style = MaterialTheme.typography.headlineSmall) }
                        item { Text("结合信号质量、心率、HRV 和活动状态，解释已记录的身体事件。") }
                        if (insights.isEmpty()) item { InfoCard("等待第一段有效记录", "保持佩戴并等待稳定数据后，解释卡片会出现在这里。") {
                            Text("未配置 AI 服务时使用本地规则，并标明来源。")
                        } }
                        items(insights, key = { it.id }) { card -> InfoCard(card.title, card.explanation) {
                            Text("${card.source} · ${DateTimeFormatter.ofPattern("MM-dd HH:mm").withZone(ZoneId.systemDefault()).format(card.createdAt)}", style = MaterialTheme.typography.labelMedium)
                        } }
                    }
                    else -> {
                        item { InfoCard("颈环设备", deviceName) {
                            Text(stateLabel(snapshot))
                            notice?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                            snapshot.lastError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                            val active = snapshot.acquisition in listOf(AcquisitionState.STARTING, AcquisitionState.RUNNING, AcquisitionState.WAITING_FOR_DATA, AcquisitionState.STOPPING)
                            Button(onClick = onScan, enabled = !scanning && !active) { Text(if (scanning) "正在扫描…" else "扫描设备") }
                            nearby.forEach { device -> OutlinedButton(onClick = { onSelect(device) }, enabled = !active) { Text("${device.name} · ${device.rssi} dBm") } }
                        } }
                        item { InfoCard("采集", "启动 App 后自动采集 · 默认开启") {
                            Text("停止仅作用于本次运行。切换页面或从后台返回不会恢复采集；重新启动 App 后自动开始。")
                            val active = snapshot.acquisition in listOf(AcquisitionState.STARTING, AcquisitionState.RUNNING, AcquisitionState.WAITING_FOR_DATA)
                            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                                Button(onClick = onStop, enabled = active) { Text("停止采集") }
                                OutlinedButton(onClick = onStart, enabled = !active && snapshot.acquisition != AcquisitionState.STOPPING) { Text("开始采集") }
                            }
                            Text("后台通知显示心率与 HRV；可直接从通知停止。", style = MaterialTheme.typography.bodySmall)
                        } }
                        item { GatewaySettings(app) }
                        item { Text("Smart Collar Companion · 0.1.0\nHRV 使用 RMSSD；本版不接入 Health Connect。", style = MaterialTheme.typography.bodySmall) }
                    }
                }
            }
        }
    }
}

@Composable private fun GatewaySettings(app: SmartCollarApplication) {
    var endpoint by remember { mutableStateOf(app.preferences.getString("insight_endpoint", "").orEmpty()) }
    var enabled by remember { mutableStateOf(app.preferences.getBoolean("cloud_insight", false)) }
    var saved by remember { mutableStateOf(false) }
    InfoCard("AI Insight 服务", "启用后，将结构化身体事件发送到你配置的服务；不上传原始 ECG。") {
        OutlinedTextField(endpoint, { endpoint = it; saved = false }, label = { Text("HTTPS 网关地址") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) { Text("启用 AI 解释"); Switch(enabled, { enabled = it; saved = false }) }
        Button(onClick = {
            app.preferences.edit().putString("insight_endpoint", endpoint.trim()).putBoolean("cloud_insight", enabled).apply()
            saved = true
        }, enabled = !enabled || endpoint.startsWith("https://")) { Text(if (saved) "已保存" else "保存") }
        Text("模型密钥由网关保管。未启用时保留本地解释卡片。", style = MaterialTheme.typography.bodySmall)
    }
}

@Composable private fun MetricCard(title: String, value: String, unit: String, modifier: Modifier, onClick: () -> Unit) {
    Card(onClick = onClick, modifier = modifier, shape = RoundedCornerShape(24.dp)) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            Text(value, style = MaterialTheme.typography.headlineLarge)
            Text(unit, style = MaterialTheme.typography.labelMedium)
        }
    }
}
@Composable private fun InfoCard(title: String, description: String, content: @Composable ColumnScope.() -> Unit) {
    Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(24.dp)) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(title, style = MaterialTheme.typography.titleLarge)
            Text(description, style = MaterialTheme.typography.bodyMedium)
            content()
        }
    }
}
@Composable private fun Waveform(points: List<Float>) {
    val color = MaterialTheme.colorScheme.primary
    if (points.size < 2) { Text("等待波形", Modifier.fillMaxWidth().height(80.dp), color = MaterialTheme.colorScheme.onSurfaceVariant); return }
    Canvas(Modifier.fillMaxWidth().height(120.dp)) {
        val low = points.minOrNull() ?: 0f
        val span = ((points.maxOrNull() ?: 0f) - low).coerceAtLeast(1f)
        val path = Path()
        points.forEachIndexed { index, point ->
            val x = size.width * index / (points.size - 1)
            val y = size.height * (0.9f - 0.8f * (point - low) / span)
            if (index == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }
        drawPath(path, color, style = Stroke(width = 2.dp.toPx()))
    }
}
private fun metric(value: Double?, decimals: Int = 1) = value?.let { "%.${decimals}f".format(it) } ?: "--"
private fun motionLabel(level: String?) = when (level) { "still" -> "静止"; "light" -> "轻度活动"; "moderate" -> "中度活动"; "vigorous" -> "较强活动"; else -> "等待足够的活动数据" }
private fun stateLabel(snapshot: CollarSnapshot) = when (snapshot.acquisition) {
    AcquisitionState.STOPPED -> "本次采集已停止"
    AcquisitionState.STARTING -> if (snapshot.connection == ConnectionState.RECONNECTING) "正在重新连接设备" else "正在连接并请求采集"
    AcquisitionState.RUNNING -> "实时采集中"
    AcquisitionState.STOPPING -> "等待设备确认停止"
    AcquisitionState.WAITING_FOR_DATA -> "设备已连接，等待新数据"
    AcquisitionState.ERROR -> "采集需要处理 · 前往 Settings"
}
