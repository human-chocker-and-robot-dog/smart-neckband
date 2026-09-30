package com.smartneckband.companion.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.smartneckband.companion.data.MetricHistoryPoint
import kotlin.math.max

@Composable
internal fun MetricHistoryChart(
    points: List<MetricHistoryPoint>,
    value: (MetricHistoryPoint) -> Double?,
    color: Color,
    modifier: Modifier = Modifier,
    emptyLabel: String = "等待更多实时数据",
) {
    val valid = points.mapNotNull { point -> value(point)?.takeIf { it.isFinite() }?.let { point to it } }
    if (valid.size < 2) {
        Box(modifier.fillMaxWidth().height(150.dp), contentAlignment = Alignment.Center) {
            Text(emptyLabel, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        return
    }
    val min = valid.minOf { it.second }
    val max = valid.maxOf { it.second }
    val span = max(max - min, 1.0)
    Canvas(modifier.fillMaxWidth().height(150.dp).padding(horizontal = 4.dp, vertical = 12.dp)) {
        val xStep = size.width / (valid.size - 1).toFloat()
        fun y(raw: Double) = size.height - ((raw - min) / span * size.height).toFloat()
        for (fraction in listOf(0.0f, .5f, 1.0f)) {
            val gridY = size.height * fraction
            drawLine(Color.Black.copy(alpha = .08f), Offset(0f, gridY), Offset(size.width, gridY), 1f)
        }
        valid.zipWithNext().forEachIndexed { index, pair ->
            drawLine(color, Offset(index * xStep, y(pair.first.second)), Offset((index + 1) * xStep, y(pair.second.second)), 4f)
        }
    }
}
