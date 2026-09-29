package com.smartneckband.companion.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.FavoriteBorder
import androidx.compose.material.icons.filled.SelfImprovement
import androidx.compose.material.icons.filled.DirectionsWalk
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.smartneckband.companion.data.*
import java.time.ZoneId
import java.time.format.DateTimeFormatter

fun LazyListScope.insightFeed(
    cards: List<InsightCard>, demo: Boolean, ecgDemo: Boolean,
    config: AiConfiguration, request: AiRequestState, showHistory: Boolean,
    onGenerate: () -> Unit, onCancel: () -> Unit, onSettings: () -> Unit, onHistory: () -> Unit,
) {
    val latest = cards.firstOrNull { it.schemaVersion >= InsightPrompt.SCHEMA_VERSION }
    val current = if (latest == null) emptyList() else cards.filter {
        it.schemaVersion >= InsightPrompt.SCHEMA_VERSION && (it.eventId ?: it.id) == (latest.eventId ?: latest.id)
    }
    val currentIds = current.map { it.id }.toSet()
    val history = cards.filter { it.id !in currentIds }
    item("insight-intro") {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Icon(Icons.Default.AutoAwesome, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(20.dp))
                Text(if (demo) "INSIGHT / 演示" else "INSIGHT / 此刻", style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary, letterSpacing = 1.sp)
            }
            Text("给此刻的你", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
            Text(if (demo) "三个预设的小场景，感受身体解读的样子。" else "读一读身体的节奏，留一点照顾自己的空间。",
                style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
    if (!demo) item("insight-actions") {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                Button(onClick = onGenerate, enabled = config.enabled && config.hasKey && !ecgDemo && !request.busy) {
                    Text(if (request.busy) "正在解读…" else "读懂此刻")
                }
                TextButton(onClick = onSettings) { Text(if (config.enabled && config.hasKey) "AI 设置" else "配置 AI") }
                if (request.busy) TextButton(onClick = onCancel) { Text("取消") }
            }
            if (request.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            if (ecgDemo) Text("ECG 演示播放中；可在 Settings 切回实时记录。", style = MaterialTheme.typography.bodySmall)
            else if (request.isError) Text(
                if (current.firstOrNull()?.source == "本地解读") "这一组来自本地解读；API 请求详情见 Settings。"
                else "本次解读尚未更新，请求详情见 Settings。",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
    if (current.isEmpty()) item("insight-empty") {
        Surface(shape = RoundedCornerShape(24.dp), color = Color(0xFFEDF3EE), modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("每一段记录，都值得被看见", style = MaterialTheme.typography.titleMedium)
                Text("身体记录到来后，这里会出现身体节奏、活动与休息、此刻建议三张卡片。也可以在 Settings 开启 AI Insight 演示。",
                    style = MaterialTheme.typography.bodyMedium)
            }
        }
    } else {
        item("insight-current-source") { InsightGroupHeader(current.first(), demo) }
        items(current, key = { "current-${it.id}" }) { InsightStoryCard(it) }
        item("insight-current-note") {
            Text(current.first().caveat.orEmpty(), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
    if (history.isNotEmpty()) {
        item("insight-history-toggle") {
            TextButton(onClick = onHistory, modifier = Modifier.fillMaxWidth()) {
                Text(if (showHistory) "收起过往记录" else "查看过往记录 · ${history.size} 张")
            }
        }
        if (showHistory) history.groupBy { it.eventId ?: it.id }.forEach { (id, group) ->
            item("history-heading-$id") { InsightGroupHeader(group.first(), demo) }
            items(group, key = { "history-${it.id}" }) { InsightStoryCard(it) }
        }
    }
}

@Composable
private fun InsightGroupHeader(card: InsightCard, demo: Boolean) {
    val time = DateTimeFormatter.ofPattern("MM-dd HH:mm").withZone(ZoneId.systemDefault()).format(card.createdAt)
    Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
        Text(if (demo) "演示 · 合成场景" else "$time · ${if (card.schemaVersion < 2) "过往记录" else "身体小记"}",
            style = MaterialTheme.typography.titleSmall)
        Text(card.source, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun InsightStoryCard(card: InsightCard) {
    val background = when (card.kind) {
        "activity" -> Color(0xFFEAF0F8)
        "suggestion" -> Color(0xFFF9EEDF)
        else -> Color(0xFFE6F1EA)
    }
    val accent = when (card.kind) {
        "activity" -> Color(0xFF405C7D)
        "suggestion" -> Color(0xFF7B5732)
        else -> Color(0xFF28675C)
    }
    val icon = when (card.kind) {
        "activity" -> Icons.Default.DirectionsWalk
        "suggestion" -> Icons.Default.SelfImprovement
        else -> Icons.Default.FavoriteBorder
    }
    val label = when (card.kind) { "activity" -> "02 / 活动与休息"; "suggestion" -> "03 / 此刻建议"; else -> "01 / 身体节奏" }
    Surface(Modifier.fillMaxWidth(), shape = RoundedCornerShape(24.dp), color = background) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Surface(shape = RoundedCornerShape(12.dp), color = Color.White.copy(alpha = .65f)) {
                    Icon(icon, null, modifier = Modifier.padding(9.dp).size(20.dp), tint = accent)
                }
                Text(label, style = MaterialTheme.typography.labelMedium, color = accent, letterSpacing = .4.sp)
            }
            Text(card.title, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold, color = Color(0xFF24352F))
            Text(card.explanation, style = MaterialTheme.typography.bodyMedium, lineHeight = 24.sp, color = Color(0xFF38453F))
            card.suggestion?.takeIf { it.isNotBlank() }?.let { action ->
                Surface(shape = RoundedCornerShape(16.dp), color = Color.White.copy(alpha = .65f)) {
                    Row(Modifier.fillMaxWidth().padding(14.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Icon(Icons.AutoMirrored.Filled.ArrowForward, null, modifier = Modifier.size(18.dp), tint = accent)
                        Text(action, style = MaterialTheme.typography.bodyMedium, color = accent, modifier = Modifier.weight(1f))
                    }
                }
            }
            val evidence = card.evidence.orEmpty().lines().filter { it.isNotBlank() }
            if (evidence.isNotEmpty()) FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                evidence.forEach { value ->
                    Surface(shape = RoundedCornerShape(10.dp), color = Color.White.copy(alpha = .55f)) {
                        Text(value, Modifier.padding(horizontal = 10.dp, vertical = 6.dp), style = MaterialTheme.typography.labelMedium, color = accent)
                    }
                }
            }
        }
    }
}
