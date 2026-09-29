package com.smartneckband.companion.data

import java.time.Instant

enum class ConnectionState { DISCONNECTED, CONNECTING, CONNECTED, RECONNECTING, ERROR }
enum class AcquisitionState { STOPPED, STARTING, RUNNING, STOPPING, WAITING_FOR_DATA, ERROR }

data class HeartSnapshot(
    val bpm: Double? = null,
    val hrvRmssdMs: Double? = null,
    val signalQuality: Double? = null,
    val leadOff: Boolean = false,
    val observedAt: Instant? = null,
)

data class ImuSnapshot(
    val motionScore: Double? = null,
    val stillRatioPercent: Double? = null,
    val level: String? = null,
    val online: Boolean = false,
    val observedAt: Instant? = null,
)

data class EcgWaveform(
    val values: List<Float> = emptyList(),
    val seconds: List<Float> = emptyList(),
    val breaks: Set<Int> = emptySet(),
)

data class CollarSnapshot(
    val connection: ConnectionState = ConnectionState.DISCONNECTED,
    val acquisition: AcquisitionState = AcquisitionState.STOPPED,
    val heart: HeartSnapshot = HeartSnapshot(),
    val imu: ImuSnapshot = ImuSnapshot(),
    val dataAgeMs: Long? = null,
    val parserCrcErrors: Long = 0,
    val packetLoss: Long = 0,
    val lastError: String? = null,
    val rawEcg: EcgWaveform = EcgWaveform(),
    val cleanEcg: EcgWaveform = EcgWaveform(),
    val ecgSampleCount: Int = 0,
    val analysisSampleCount: Int = 0,
    val effectiveSampleRateHz: Double? = null,
    val timingWarning: Boolean = false,
    val analysisMessage: String = "等待设备数据",
    val hrvWindowSeconds: Int = 0,
    val rrCount: Int = 0,
    val hrvStatus: String = "waiting",
    val hrvReasons: List<String> = listOf("等待实时数据"),
    val hrvReferenceRmssdMs: Double? = null,
    val hrvReferenceRrCount: Int = 0,
    val hrvReferenceWindowSeconds: Double = 0.0,
    val hrvReferenceReasons: List<String> = emptyList(),
    val adcClippedSamples: Int = 0,
    val adcFlaggedSampleSlots: Int = 0,
)

// Display may use a labelled reference; analysis/AI continue reading qualified heart.hrvRmssdMs.
val CollarSnapshot.displayHrvRmssdMs: Double? get() = heart.hrvRmssdMs ?: hrvReferenceRmssdMs
val CollarSnapshot.isHrvReference: Boolean get() = heart.hrvRmssdMs == null && hrvReferenceRmssdMs != null

data class BodyEvent(
    val id: String,
    val type: String,
    val severity: String,
    val summary: String,
    val observedAt: Instant,
    val evidence: Map<String, Any?> = emptyMap(),
)

data class InsightCard(
    val id: String,
    val title: String,
    val explanation: String,
    val source: String,
    val eventId: String? = null,
    val createdAt: Instant = Instant.now(),
    val evidence: String? = null,
    val caveat: String? = null,
    val suggestion: String? = null,
)
