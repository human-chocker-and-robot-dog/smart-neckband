package com.smartneckband.companion.data

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import org.json.JSONObject
import java.time.Instant

interface CollarTransport {
    suspend fun connect(deviceId: String)
    suspend fun disconnect()
    suspend fun startAcquisition()
    suspend fun stopAcquisition()
}

/** UI state only. The foreground service is the single transport/analysis owner. */
class CollarRepository {
    private val mutable = MutableStateFlow(CollarSnapshot())
    val snapshot = mutable.asStateFlow()
    private val cards = MutableStateFlow<List<InsightCard>>(emptyList())
    val insights = cards.asStateFlow()
    private val history = MutableStateFlow<List<MetricHistoryPoint>>(emptyList())
    val metricHistory = history.asStateFlow()

    fun state(connection: ConnectionState, acquisition: AcquisitionState, error: String? = null) {
        history.value = emptyList()
        mutable.update { it.copy(connection = connection, acquisition = acquisition, lastError = error,
            heart = HeartSnapshot(), imu = ImuSnapshot(), dataAgeMs = null,
            rawEcg = EcgWaveform(), cleanEcg = EcgWaveform(), hrvWindowSeconds = 0, rrCount = 0,
            hrvReferenceRmssdMs = null, hrvReferenceRrCount = 0, hrvReferenceWindowSeconds = 0.0,
            hrvReferenceReasons = emptyList(), adcClippedSamples = 0, adcFlaggedSampleSlots = 0,
            ecgSampleCount = 0, analysisSampleCount = 0, effectiveSampleRateHz = null, timingWarning = false,
            hrvStatus = "waiting", hrvReasons = listOf(if (acquisition == AcquisitionState.STOPPED) "本次采集已停止" else "等待实时 ECG 与 IMU 数据"),
            analysisMessage = if (acquisition == AcquisitionState.STOPPED) "本次采集已停止" else "等待设备数据") }
    }

    fun analysis(json: JSONObject) {
        fun number(key: String): Double? = if (json.isNull(key)) null else json.optDouble(key).takeIf { it.isFinite() }
        fun waveform(key: String): EcgWaveform {
            val objectValue = json.optJSONObject(key) ?: return EcgWaveform()
            val values = objectValue.optJSONArray("values") ?: return EcgWaveform()
            val seconds = objectValue.optJSONArray("seconds") ?: return EcgWaveform()
            if (values.length() != seconds.length()) return EcgWaveform()
            val points = (0 until values.length()).map { values.optDouble(it).toFloat() }
            val times = (0 until seconds.length()).map { seconds.optDouble(it).toFloat() }
            if (points.any { !it.isFinite() } || times.any { !it.isFinite() }) return EcgWaveform()
            val breaks = objectValue.optJSONArray("breaks")?.let { a ->
                (0 until a.length()).map { a.optInt(it) }.toSet()
            } ?: emptySet()
            return EcgWaveform(points, times, breaks)
        }
        val age = number("age_ms")?.toLong()
        val fresh = age != null && age < 3000
        if (fresh) {
            val point = MetricHistoryPoint(
                observedAt = Instant.now(), bpm = number("bpm"), hrvRmssdMs = number("rmssd") ?: number("rmssd_reference"),
                motionScore = number("motion"), stillRatioPercent = number("still"))
            if (point.bpm != null || point.hrvRmssdMs != null || point.motionScore != null) {
                history.update { (it + point).takeLast(180) }
            }
        }
        mutable.update { previous -> previous.copy(
            acquisition = if (fresh) AcquisitionState.RUNNING else AcquisitionState.WAITING_FOR_DATA,
            heart = HeartSnapshot(number("bpm"), number("rmssd"), number("quality"), json.optBoolean("lead_off"),
                if (fresh) Instant.now().minusMillis(age!!) else null),
            imu = ImuSnapshot(number("motion"), number("still"), json.optString("motion_level").takeUnless { it == "null" },
                json.optBoolean("imu_online"), if (json.optBoolean("imu_online")) Instant.now() else null),
            dataAgeMs = age, rawEcg = waveform("raw"), cleanEcg = waveform("cleaned"),
            ecgSampleCount = json.optInt("sample_count"), analysisSampleCount = json.optInt("analysis_sample_count"),
            effectiveSampleRateHz = number("effective_rate_hz"), timingWarning = json.optBoolean("timing_warning"),
            analysisMessage = json.optString("message"), hrvWindowSeconds = json.optInt("hrv_window_s"),
            rrCount = json.optInt("rr_count"), hrvStatus = json.optString("hrv_status", "waiting"),
            hrvReferenceRmssdMs = if (fresh) number("rmssd_reference")?.takeIf { it >= 0 } else null,
            hrvReferenceRrCount = json.optInt("hrv_reference_rr_count"),
            hrvReferenceWindowSeconds = number("hrv_reference_window_s") ?: 0.0,
            hrvReferenceReasons = json.optJSONArray("hrv_reference_reasons")?.let { reasons ->
                (0 until reasons.length()).map { reasons.getString(it) }
            } ?: emptyList(),
            adcClippedSamples = json.optInt("adc_clipped_samples"),
            adcFlaggedSampleSlots = json.optInt("adc_flagged_sample_slots"),
            hrvReasons = json.optJSONArray("hrv_reasons")?.let { reasons ->
                (0 until reasons.length()).map { reasons.getString(it) }
            } ?: listOf(json.optString("message", "等待有效数据"))) }
    }

    fun transportStats(crcErrors: Long, lostPackets: Long) {
        mutable.update { it.copy(parserCrcErrors = crcErrors, packetLoss = lostPackets) }
    }
    fun analysisError(message: String) {
        mutable.update { it.copy(heart = HeartSnapshot(), analysisMessage = message, cleanEcg = EcgWaveform(),
            hrvReferenceRmssdMs = null, hrvReferenceRrCount = 0, hrvReferenceWindowSeconds = 0.0,
            hrvReferenceReasons = listOf(message),
            hrvStatus = "blocked", hrvReasons = listOf(message)) }
    }
    fun setInsights(value: List<InsightCard>) { cards.value = value.take(30) }
    fun addInsight(card: InsightCard) { cards.update { (listOf(card) + it).take(30) } }
    fun addInsights(batch: List<InsightCard>) { cards.update { (batch + it).take(30) } }
}
