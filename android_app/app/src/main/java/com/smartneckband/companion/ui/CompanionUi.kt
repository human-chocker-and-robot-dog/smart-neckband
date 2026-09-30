package com.smartneckband.companion.ui

import android.app.Activity
import android.widget.Toast
import androidx.activity.compose.BackHandler
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import com.smartneckband.companion.NearbyCollar
import com.smartneckband.companion.BuildConfig
import com.smartneckband.companion.SmartCollarApplication
import com.smartneckband.companion.data.*
import kotlinx.coroutines.delay
import java.time.Instant
import kotlin.math.PI
import kotlin.math.sin

private val palette = lightColorScheme(primary = Color(0xFF28675C), secondary = Color(0xFF666D51),
    background = Color(0xFFF8FAF6), surface = Color(0xFFF8FAF6), surfaceContainer = Color(0xFFEDF2EB))

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CompanionUi(app: SmartCollarApplication, deviceName: String, nearby: List<NearbyCollar>, scanning: Boolean,
    notice: String?, onScan: () -> Unit, onSelect: (NearbyCollar) -> Unit, onStart: () -> Unit, onStop: () -> Unit,
    onCapture: () -> Unit = {}, onShareCapture: () -> Unit = {}, captureNotice: String? = null) {
    val engine = remember(app) { app.insightEngine }
    val aiRequest by engine.state.collectAsStateWithLifecycle()
    val aiConfig by app.aiSettings.state.collectAsStateWithLifecycle()
    val uiScope = rememberCoroutineScope()
    val liveSnapshot by app.repository.snapshot.collectAsStateWithLifecycle()
    val liveMetricHistory by app.repository.metricHistory.collectAsStateWithLifecycle()
    val liveInsights by app.repository.insights.collectAsStateWithLifecycle()
    val ecgDemo by app.ecgDemo.collectAsStateWithLifecycle()
    val insightDemo by app.insightDemo.collectAsStateWithLifecycle()
    var demoSeconds by remember { mutableDoubleStateOf(0.0) }
    val lifecycleOwner = androidx.lifecycle.compose.LocalLifecycleOwner.current
    LaunchedEffect(ecgDemo, lifecycleOwner) {
        if (ecgDemo) lifecycleOwner.lifecycle.repeatOnLifecycle(androidx.lifecycle.Lifecycle.State.STARTED) {
            while (true) { delay(100); demoSeconds += .1 }
        }
    }
    val snapshot = if (ecgDemo) remember(demoSeconds) { DemoData.snapshot(demoSeconds) } else liveSnapshot
    val metricHistory = if (ecgDemo) remember(demoSeconds) {
        (0..59).map { index ->
            val phase = (index + demoSeconds) / 8.0
            MetricHistoryPoint(Instant.now().minusSeconds((59 - index).toLong()),
                bpm = 72.0 + 3.0 * sin(phase), hrvRmssdMs = 42.0 + 4.0 * sin(phase / 2),
                motionScore = 4.0 + 1.5 * sin(phase), stillRatioPercent = 96.0)
        }
    } else liveMetricHistory
    val insights = if (insightDemo) DemoData.cards else liveInsights
    var page by rememberSaveable { mutableStateOf("Today") }
    var detail by rememberSaveable { mutableStateOf<String?>(null) }
    var waveformSeconds by rememberSaveable { mutableIntStateOf(3) }
    var automaticScale by rememberSaveable { mutableStateOf(true) }
    var showInsightHistory by rememberSaveable { mutableStateOf(false) }
    var lastBackAt by rememberSaveable { mutableLongStateOf(0L) }
    val context = LocalContext.current
    BackHandler {
        if (detail != null) {
            detail = null
        } else {
            val now = System.currentTimeMillis()
            if (now - lastBackAt < 2_000L) {
                (context as? Activity)?.finish()
            } else {
                lastBackAt = now
                Toast.makeText(context, "再按一次退出应用", Toast.LENGTH_SHORT).show()
            }
        }
    }
    MaterialTheme(colorScheme = palette) {
        Scaffold(topBar = { TopAppBar(title = { Text(detailTitle(detail, page)) },
            navigationIcon = { if (detail != null) IconButton(onClick = { detail = null }) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, "返回")
            } }) }, bottomBar = {
            NavigationBar {
                listOf("Today" to Icons.Default.FavoriteBorder, "AI Insight" to Icons.Default.AutoAwesome, "Settings" to Icons.Default.Settings).forEach { (name, icon) ->
                    NavigationBarItem(selected = page == name, onClick = { page = name; detail = null },
                        icon = { Icon(icon, null) }, label = { Text(name) })
                }
            }
        }) { padding ->
            key(page, detail) {
            LazyColumn(Modifier.fillMaxSize().padding(padding), contentPadding = PaddingValues(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                if (ecgDemo && (page == "Today" || detail != null)) item {
                    Text("演示模式 · 合成 ECG / 心率，非实际测量", color = MaterialTheme.colorScheme.primary)
                }
                if (detail != null) {
                    when (detail) {
                        "heart" -> {
                            item { MetricDetailHeader("心率", metric(snapshot.heart.bpm, 0), "BPM", "心率表示当前心搏频率；曲线来自最近实时分析窗口。") }
                            item { InfoCard("近期心率", "最近 ${metricHistory.size} 个实时分析点") {
                                MetricHistoryChart(metricHistory, { it.bpm }, Color(0xFF28675C))
                            } }
                            item { InfoCard("当前数据", "${snapshot.dataAgeMs?.let { "数据延迟 ${it} ms" } ?: "等待实时数据"}") {
                                Text("信号质量：${snapshot.heart.signalQuality?.let { "%.0f%%".format(it * 100) } ?: "--"}")
                                Text("心率只描述频率，不单独推断活动原因或身体状态。", style = MaterialTheme.typography.bodySmall)
                            } }
                        }
                        "hrv" -> {
                            item { MetricDetailHeader("HRV · RMSSD", hrvMetric(snapshot), "ms", "RMSSD 描述相邻心搏间期差值的变化；它和心率是两个不同维度。") }
                            item { InfoCard("近期 RMSSD", "最近 ${metricHistory.count { it.hrvRmssdMs != null }} 个有效分析点") {
                                MetricHistoryChart(metricHistory, { it.hrvRmssdMs }, Color(0xFF405C7D))
                            } }
                            item { InfoCard("计算窗口", if (snapshot.isHrvReference) "短窗口参考值" else hrvLabel(snapshot)) {
                                Text("${hrvMetric(snapshot)} ms", style = MaterialTheme.typography.headlineMedium)
                                Text("静息窗口 ${snapshot.hrvWindowSeconds}/60 秒 · 有效 RR ${snapshot.rrCount}/30 个")
                                if (snapshot.isHrvReference) Text("当前值带 ≈ 标记，表示短窗口参考值；不换算为压力分数。", style = MaterialTheme.typography.bodySmall)
                                snapshot.hrvReasons.take(2).forEach { reason -> Text("· $reason", style = MaterialTheme.typography.bodySmall) }
                            } }
                        }
                        "motion" -> {
                            item { MetricDetailHeader("身体活动", metric(snapshot.imu.motionScore), "活动指数", "活动指数描述近期动作幅度；静止占比描述低活动时间桶的比例。") }
                            item { InfoCard("近期活动幅度", "最近 ${metricHistory.count { it.motionScore != null }} 个实时分析点") {
                                MetricHistoryChart(metricHistory, { it.motionScore }, Color(0xFF7B5732))
                            } }
                            item { InfoCard("静止占比", "最近窗口") {
                                MetricHistoryChart(metricHistory, { it.stillRatioPercent }, Color(0xFF6D5C88))
                                Text("静止占比为 100% 也不等于完全没有动作。", style = MaterialTheme.typography.bodySmall)
                            } }
                        }
                        else -> {
                            item {
                                WaveformControls(waveformSeconds, automaticScale, { waveformSeconds = it }, { automaticScale = it })
                                Text("缩放仅改变显示；保留全部 ${snapshot.ecgSampleCount} 个采样。", style = MaterialTheme.typography.bodySmall)
                            }
                            item { InfoCard("ECG · 原始 ADC", "完整缓存 ${snapshot.ecgSampleCount} 个采样 · 原始计数") {
                                Waveform(snapshot.rawEcg, raw = true, windowSeconds = waveformSeconds, automatic = automaticScale)
                            } }
                            item { InfoCard("ECG · 滤波波形", snapshot.analysisMessage) {
                                Waveform(snapshot.cleanEcg, windowSeconds = waveformSeconds, automatic = automaticScale)
                            } }
                            item { InfoCard("数据质量", "${snapshot.dataAgeMs?.let { "数据延迟 ${it} ms" } ?: "暂无实时数据"}") {
                                Text("信号质量：${snapshot.heart.signalQuality?.let { "%.0f%%".format(it * 100) } ?: "--"}")
                                Text("CRC 错误 ${snapshot.parserCrcErrors} · 丢包 ${snapshot.packetLoss}")
                                Text("实测采样率 ${metric(snapshot.effectiveSampleRateHz, 0)} Hz · 标称 500 Hz")
                                Text(if (ecgDemo) "演示窗口 ${snapshot.ecgSampleCount} 点" else "分析窗口 ${snapshot.analysisSampleCount}/5000 点")
                                Text("原始 ADC 实际触顶 ${snapshot.adcClippedSamples}/${snapshot.analysisSampleCount} 点（0 或 4095）")
                                if (snapshot.adcFlaggedSampleSlots > 0) Text("包级削顶标记覆盖 ${snapshot.adcFlaggedSampleSlots} 个采样位置，不等于实际触顶点数。", style = MaterialTheme.typography.bodySmall)
                            } }
                        }
                    }
                } else when (page) {
                    "Today" -> {
                        item { Text("今天的数据", style = MaterialTheme.typography.headlineSmall) }
                        item { Text(if (ecgDemo) "演示播放中 · 72 BPM" else stateLabel(snapshot), color = MaterialTheme.colorScheme.primary) }
                        item { Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            MetricCard("心率", metric(snapshot.heart.bpm, 0), "BPM", Modifier.weight(1f)) { detail = "heart" }
                            MetricCard(if (snapshot.isHrvReference) "HRV · 参考" else "HRV", hrvMetric(snapshot), "RMSSD · ms", Modifier.weight(1f)) { detail = "hrv" }
                        } }
                        if (snapshot.heart.hrvRmssdMs == null) item {
                            Text("HRV ${hrvLabel(snapshot)}：${snapshot.hrvReasons.joinToString("；")}", style = MaterialTheme.typography.bodyMedium)
                        }
                        item { InfoCard("近期趋势", "首页保留最近实时分析点；点卡片查看完整曲线") {
                            Text("心率", style = MaterialTheme.typography.labelLarge)
                            MetricHistoryChart(metricHistory, { it.bpm }, Color(0xFF28675C), Modifier.height(92.dp))
                            Text("HRV · RMSSD", style = MaterialTheme.typography.labelLarge)
                            MetricHistoryChart(metricHistory, { it.hrvRmssdMs }, Color(0xFF405C7D), Modifier.height(92.dp))
                        } }
                        item { Card(onClick = { detail = "ecg" }, shape = RoundedCornerShape(24.dp)) {
                            Column(Modifier.fillMaxWidth().padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                                Text("ECG", style = MaterialTheme.typography.titleLarge)
                                WaveformControls(waveformSeconds, automaticScale, { waveformSeconds = it }, { automaticScale = it })
                                val showRaw = snapshot.cleanEcg.values.isEmpty()
                                Waveform(if (showRaw) snapshot.rawEcg else snapshot.cleanEcg, raw = showRaw,
                                    windowSeconds = waveformSeconds, automatic = automaticScale)
                                Text(snapshot.analysisMessage, style = MaterialTheme.typography.bodyMedium)
                                Text("查看原始波形与详情 →", color = MaterialTheme.colorScheme.primary)
                            }
                        } }
                        item { Card(onClick = { detail = "motion" }, shape = RoundedCornerShape(24.dp)) {
                            Column(Modifier.fillMaxWidth().padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                                Text("身体活动", style = MaterialTheme.typography.titleLarge)
                            Text("活动指数 ${metric(snapshot.imu.motionScore)} · 静止占比 ${metric(snapshot.imu.stillRatioPercent, 0)}%")
                            Text(if (snapshot.imu.online) "IMU 在线" else "等待 IMU 数据", style = MaterialTheme.typography.bodySmall)
                                Text("查看活动历史 →", color = MaterialTheme.colorScheme.primary)
                            }
                        } }
                        item { Text(if (ecgDemo) "演示数值不进入采集记录；可在 Settings 关闭演示。" else snapshot.lastError ?: if (snapshot.connection == ConnectionState.DISCONNECTED) "设备配置和采集控制位于 Settings。" else "数据来自颈环的实时 ECG 与 IMU。", style = MaterialTheme.typography.bodySmall) }
                    }
                    "AI Insight" -> {
                        insightFeed(insights, insightDemo, ecgDemo, aiConfig, aiRequest, showInsightHistory,
                            onGenerate = engine::generateNow, onCancel = { engine.cancelActive() },
                            onSettings = { page = "Settings" }, onHistory = { showInsightHistory = !showInsightHistory })
                    }
                    else -> {
                        item { InfoCard("演示模式", "两个开关独立生效，无需连接设备或配置 API。") {
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                                Text("AI Insight 演示")
                                Switch(insightDemo, app::setInsightDemo, Modifier.semantics { contentDescription = "AI Insight 演示开关" })
                            }
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                                Text("ECG / 心率演示")
                                Switch(ecgDemo, app::setEcgDemo, Modifier.semantics { contentDescription = "ECG 心率演示开关" })
                            }
                            Text("开启后到 Today 或 AI Insight 查看。演示不写入真实记录，也不请求 AI API。", style = MaterialTheme.typography.bodySmall)
                            if (ecgDemo) Text("已在运行的真实采集会继续，可在下方停止。下次启动 App 保持演示，不自动连接设备。", style = MaterialTheme.typography.bodySmall)
                        } }
                        item { AiSettingsCard(app, uiScope) }
                        item { InfoCard("颈环设备", deviceName) {
                            Text(stateLabel(liveSnapshot))
                            notice?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                            liveSnapshot.lastError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                            val active = liveSnapshot.acquisition in listOf(AcquisitionState.STARTING, AcquisitionState.RUNNING, AcquisitionState.WAITING_FOR_DATA, AcquisitionState.STOPPING)
                            Button(onClick = onScan, enabled = !scanning && !active) { Text(if (scanning) "正在扫描…" else "扫描设备") }
                            nearby.forEach { device -> OutlinedButton(onClick = { onSelect(device) }, enabled = !active) { Text("${device.name} · ${device.rssi} dBm") } }
                        } }
                        item { InfoCard("真实采集", if (ecgDemo) "演示展示中 · 下方控制真实设备" else "启动 App 后自动采集 · 默认开启") {
                            Text("停止仅作用于本次运行。切换页面或从后台返回不会恢复采集；重新启动 App 后自动开始。")
                            val active = liveSnapshot.acquisition in listOf(AcquisitionState.STARTING, AcquisitionState.RUNNING, AcquisitionState.WAITING_FOR_DATA)
                            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                                Button(onClick = onStop, enabled = active) { Text("停止采集") }
                                OutlinedButton(onClick = onStart, enabled = !active && liveSnapshot.acquisition != AcquisitionState.STOPPING) { Text("开始采集") }
                            }
                            Text("后台通知显示心率与 HRV；可直接从通知停止。", style = MaterialTheme.typography.bodySmall)
                        } }
                        item { InfoCard("波形诊断", "保存最近 60 秒真实接收字节、解码样本、分析结果与错误；演示波形不进入日志。") {
                            Button(onClick = onCapture) { Text("捕获诊断") }
                            OutlinedButton(onClick = onShareCapture) { Text("分享最近一次捕获") }
                            captureNotice?.let { Text(it) }
                            Text("仅保存在本机，包含传感器数据。未采集时日志可能为空；再次捕获会替换上次文件。", style = MaterialTheme.typography.bodySmall)
                        } }
                        item { Text("Smart Collar Companion · ${BuildConfig.VERSION_NAME}\nHRV 使用 RMSSD；本版不接入 Health Connect。", style = MaterialTheme.typography.bodySmall) }
                    }
                }
            }
            }
        }
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
@Composable private fun MetricDetailHeader(title: String, value: String, unit: String, description: String) {
    InfoCard(title, description) {
        Text(value, style = MaterialTheme.typography.displaySmall)
        Text(unit, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
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
private fun hrvMetric(snapshot: CollarSnapshot) = (if (snapshot.isHrvReference) "≈ " else "") + metric(snapshot.displayHrvRmssdMs)
private fun hrvLabel(snapshot: CollarSnapshot) = if (snapshot.isHrvReference) "参考值" else when (snapshot.hrvStatus) {
    "ready" -> "已就绪"
    "collecting" -> "积累中"
    "blocked" -> "暂停"
    "demo" -> "演示数据"
    else -> "等待数据"
}
private fun metric(value: Double?, decimals: Int = 1) = value?.let { "%.${decimals}f".format(it) } ?: "--"
private fun motionLabel(level: String?) = when (level) { "still" -> "静止"; "light" -> "轻度活动"; "moderate" -> "中度活动"; "vigorous" -> "较强活动"; else -> "等待足够的活动数据" }
private fun detailTitle(detail: String?, page: String) = when (detail) {
    "heart" -> "心率详情"
    "hrv" -> "HRV 详情"
    "motion" -> "活动详情"
    "ecg" -> "ECG 详情"
    else -> page
}
private fun stateLabel(snapshot: CollarSnapshot) = when (snapshot.acquisition) {
    AcquisitionState.STOPPED -> "本次采集已停止"
    AcquisitionState.STARTING -> if (snapshot.connection == ConnectionState.RECONNECTING) "正在重新连接设备" else "正在连接并请求采集"
    AcquisitionState.RUNNING -> "实时采集中"
    AcquisitionState.STOPPING -> "等待设备确认停止"
    AcquisitionState.WAITING_FOR_DATA -> "设备已连接，等待新数据"
    AcquisitionState.ERROR -> "采集需要处理 · 前往 Settings"
}
