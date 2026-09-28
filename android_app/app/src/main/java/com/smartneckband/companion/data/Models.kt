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

data class CollarSnapshot(
    val connection: ConnectionState = ConnectionState.DISCONNECTED,
    val acquisition: AcquisitionState = AcquisitionState.STOPPED,
    val heart: HeartSnapshot = HeartSnapshot(),
    val imu: ImuSnapshot = ImuSnapshot(),
    val dataAgeMs: Long? = null,
    val parserCrcErrors: Long = 0,
    val packetLoss: Long = 0,
    val lastError: String? = null,
    val rawEcg: List<Float> = emptyList(),
    val cleanEcg: List<Float> = emptyList(),
    val analysisMessage: String = "等待设备数据",
    val hrvWindowSeconds: Int = 0,
    val rrCount: Int = 0,
)

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
)
