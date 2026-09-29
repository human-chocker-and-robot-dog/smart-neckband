package com.smartneckband.companion.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.unit.dp
import com.smartneckband.companion.data.EcgWaveform
import kotlin.math.*

@Composable
internal fun WaveformControls(seconds: Int, automatic: Boolean, onSeconds: (Int) -> Unit, onAutomatic: (Boolean) -> Unit) {
    Column {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf(3, 5, 10).forEach { value ->
                FilterChip(seconds == value, { onSeconds(value) }, label = { Text("${value} 秒") })
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(automatic, { onAutomatic(true) }, label = { Text("自动幅度") })
            FilterChip(!automatic, { onAutomatic(false) }, label = { Text("固定刻度") })
        }
    }
}

private data class ChartRange(val low: Float, val high: Float)

private fun niceCeiling(value: Float): Float {
    val magnitude = 10f.pow(floor(log10(value.coerceAtLeast(1f))))
    val fraction = value / magnitude
    return listOf(1f, 1.25f, 1.5f, 2f, 2.5f, 3f, 4f, 5f, 6f, 8f, 10f)
        .first { it >= fraction } * magnitude
}

/** Viewport only: no resampling, normalization or changes to acquisition buffers. */
@Composable
internal fun Waveform(series: EcgWaveform, raw: Boolean = false, windowSeconds: Int = 3, automatic: Boolean = true) {
    val color = MaterialTheme.colorScheme.primary
    if (series.values.size < 2 || series.seconds.size != series.values.size) {
        Text("等待波形", Modifier.fillMaxWidth().height(100.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
        return
    }
    val end = series.seconds.last()
    val start = end - windowSeconds
    // Keep the point just before the left boundary so the crossing segment is drawn.
    val first = (series.seconds.indexOfFirst { it >= start }.coerceAtLeast(0) - 1).coerceAtLeast(0)
    val range = remember(series, first, raw, automatic) {
        if (!automatic) {
            if (raw) ChartRange(0f, 4095f) else ChartRange(-2048f, 2048f)
        } else {
            val visible = series.values.subList(first, series.values.size)
            val minimum = visible.minOrNull() ?: 0f
            val maximum = visible.maxOrNull() ?: 1f
            if (raw) {
                val span = (maximum - minimum).coerceAtLeast(200f)
                val step = niceCeiling(span / 5f)
                ChartRange((floor((minimum - span * .05f) / step) * step).coerceAtLeast(0f),
                    (ceil((maximum + span * .05f) / step) * step).coerceAtMost(4095f))
            } else {
                // Symmetric rounded scale keeps zero and the amplitude reference clear.
                val bound = niceCeiling(max(abs(minimum), abs(maximum)).coerceAtLeast(250f) * 1.05f)
                ChartRange(-bound, bound)
            }
        }
    }
    val span = (range.high - range.low).coerceAtLeast(1f)
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("最近 ${windowSeconds} 秒 · ${if (raw) "原始 ADC 计数" else "滤波后 ADC 计数"}", style = MaterialTheme.typography.labelSmall)
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Column(Modifier.width(42.dp).height(180.dp).padding(vertical = 4.dp), verticalArrangement = Arrangement.SpaceBetween) {
                listOf(range.high, (range.high + range.low) / 2, range.low).forEach {
                    Text("%.0f".format(it), style = MaterialTheme.typography.labelSmall)
                }
            }
            Canvas(Modifier.weight(1f).height(180.dp)) {
                for (tick in 0..4) {
                    val y = size.height * (.05f + .9f * tick / 4)
                    drawLine(color.copy(alpha = .13f), Offset(0f, y), Offset(size.width, y))
                }
                val tickSeconds = if (windowSeconds <= 5) .5f else 1f
                for (tick in 0..(windowSeconds / tickSeconds).toInt()) {
                    val x = size.width * tick * tickSeconds / windowSeconds
                    drawLine(color.copy(alpha = .13f), Offset(x, 0f), Offset(x, size.height))
                }
                val path = Path()
                for (index in first until series.values.size) {
                    val x = size.width * (series.seconds[index] - start) / windowSeconds
                    val y = size.height * (.95f - .9f * (series.values[index] - range.low) / span)
                    if (index == first || index in series.breaks) path.moveTo(x, y) else path.lineTo(x, y)
                }
                clipRect { drawPath(path, color, style = Stroke(width = .8.dp.toPx())) }
            }
        }
        Row(Modifier.fillMaxWidth().padding(start = 48.dp), horizontalArrangement = Arrangement.SpaceBetween) {
            Text("−${windowSeconds} s", style = MaterialTheme.typography.labelSmall)
            Text("−${"%.1f".format(windowSeconds / 2f)} s", style = MaterialTheme.typography.labelSmall)
            Text("最新 · 0 s", style = MaterialTheme.typography.labelSmall)
        }
        if (!automatic && series.values.subList(first, series.values.size).any { it < range.low || it > range.high }) {
            Text("部分波形超出固定刻度，可切换自动幅度查看。", style = MaterialTheme.typography.labelSmall)
        }
    }
}
