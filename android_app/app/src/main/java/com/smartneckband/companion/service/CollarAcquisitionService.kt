package com.smartneckband.companion.service

import android.Manifest
import android.app.*
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.IBinder
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import com.smartneckband.companion.MainActivity
import com.smartneckband.companion.R
import com.smartneckband.companion.SmartCollarApplication
import com.smartneckband.companion.data.*
import com.smartneckband.companion.protocol.*
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import org.json.JSONObject

class CollarAcquisitionService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val analysisDispatcher = Dispatchers.Default.limitedParallelism(1)
    private val app get() = application as SmartCollarApplication
    private val repository get() = app.repository
    private lateinit var transport: BleCollarTransport
    private lateinit var insights: InsightEngine
    private var session: Job? = null
    private var insightJob: Job? = null
    @Volatile private var disconnectSignal = CompletableDeferred<String>()
    @Volatile private var accepting = false
    private var stopping = false
    private var lastSequence: Long? = null
    private var packetLoss = 0L
    private data class ReceivedFrame(val frame: V0Frame, val receivedAtMs: Long)
    private val frames = Channel<ReceivedFrame>(512)

    companion object {
        const val START = "com.smartneckband.START"
        const val STOP = "com.smartneckband.STOP"
        private const val CHANNEL = "live_acquisition"
        private const val NOTIFICATION = 1001
    }

    override fun onCreate() {
        super.onCreate()
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, "颈环实时采集", NotificationManager.IMPORTANCE_LOW))
        insights = InsightEngine(app.preferences, repository)
        transport = BleCollarTransport(this, { frame, stats ->
            app.diagnostics.frame(frame, stats, accepting)
            synchronized(frames) {
                lastSequence?.let { previous ->
                    val gap = (frame.sequence - previous - 1) and 0xFFFF_FFFFL
                    if (gap in 1..0x7FFF_FFFFL) packetLoss += gap
                }
                lastSequence = frame.sequence
                repository.transportStats(stats.crcErrors, packetLoss)
            }
            if (accepting && (frame is V0Frame.Ecg || frame is V0Frame.Imu)) {
                if (frames.trySend(ReceivedFrame(frame, SystemClock.elapsedRealtime())).isFailure) disconnectSignal.complete("手机处理队列已满，重新建立连续数据窗口")
            }
        }, { reason ->
            app.diagnostics.add("state", JSONObject().put("state", "disconnected").put("reason", reason))
            disconnectSignal.complete(reason)
        }, app.diagnostics::transport)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == STOP) {
            app.launchPolicy.stop()
            if (!stopping) scope.launch { stopSession() }
            return START_NOT_STICKY
        }
        if (intent?.action != START || session?.isActive == true || stopping) return START_NOT_STICKY
        val permission = if (Build.VERSION.SDK_INT >= 31) Manifest.permission.BLUETOOTH_CONNECT else Manifest.permission.BLUETOOTH
        if (ContextCompat.checkSelfPermission(this, permission) != PackageManager.PERMISSION_GRANTED) {
            repository.state(ConnectionState.ERROR, AcquisitionState.ERROR, "需要附近设备权限")
            stopSelf()
            return START_NOT_STICKY
        }
        val device = app.preferences.getString("device_address", null)
        if (device.isNullOrBlank()) { stopSelf(); return START_NOT_STICKY }
        startForeground(NOTIFICATION, notification())
        app.launchPolicy.manualStart()
        session = scope.launch { runSession(device) }
        return START_NOT_STICKY
    }

    private suspend fun runSession(address: String) {
        var failures = 0
        while (currentCoroutineContext().isActive) {
            try {
                accepting = false
                while (frames.tryReceive().isSuccess) { /* discard previous session */ }
                disconnectSignal = CompletableDeferred()
                lastSequence = null
                app.diagnostics.beginConnection()
                repository.state(if (failures == 0) ConnectionState.CONNECTING else ConnectionState.RECONNECTING, AcquisitionState.STARTING)
                transport.connect(address)
                transport.startAcquisition()
                repository.state(ConnectionState.CONNECTED, AcquisitionState.WAITING_FOR_DATA)
                val engine = withContext(analysisDispatcher) {
                    if (!Python.isStarted()) Python.start(AndroidPlatform(this@CollarAcquisitionService))
                    Python.getInstance().getModule("collar_engine").callAttr("Engine")
                }
                accepting = true
                var analyzedAt = 0L
                var lastDataAt = SystemClock.elapsedRealtime()
                val connectedAt = lastDataAt
                var notifiedAt = 0L
                while (currentCoroutineContext().isActive) {
                    if (disconnectSignal.isCompleted) error(disconnectSignal.await())
                    val received = withTimeoutOrNull(100) { frames.receive() }
                    if (received != null) {
                        // Drain pending notifications before another analysis so
                        // a slow calculation cannot repeatedly analyze old data.
                        val pending = mutableListOf(received)
                        while (pending.size < 512) pending += frames.tryReceive().getOrNull() ?: break
                        withContext(analysisDispatcher) {
                            pending.forEach { item ->
                                val frame = item.frame
                                val age = SystemClock.elapsedRealtime() - item.receivedAtMs
                                check(age < 2000) { "分析处理落后于实时数据，重新建立窗口" }
                                engine.callAttr("append", batchJson(frame).put("received_age_ms", age).toString())
                                app.diagnostics.add("processed", batchJson(frame).put("received_age_ms", age))
                            }
                        }
                        pending.lastOrNull { it.frame is V0Frame.Ecg && it.frame.flags and 0x80 == 0 }
                            ?.let { lastDataAt = it.receivedAtMs }
                    }
                    val now = SystemClock.elapsedRealtime()
                    if (now - lastDataAt > 15_000) error("设备持续无实时 ECG 数据")
                    if (now - connectedAt > 30_000) failures = 0
                    if (now - analyzedAt >= 1000) {
                        val result = withContext(analysisDispatcher) { engine.callAttr("analyze").toString() }
                        repository.analysis(JSONObject(result))
                        app.diagnostics.add("analysis", JSONObject(result))
                        analyzedAt = SystemClock.elapsedRealtime()
                        if (insightJob?.isActive != true) {
                            val snapshot = repository.snapshot.value
                            insightJob = scope.launch { insights.observe(snapshot) }
                        }
                    }
                    if (now - notifiedAt >= 2000) { updateNotification(); notifiedAt = now }
                }
            } catch (error: Exception) {
                if (error is CancellationException && error !is TimeoutCancellationException) throw error
                accepting = false
                transport.disconnect()
                failures++
                repository.state(ConnectionState.ERROR, AcquisitionState.ERROR, error.message ?: "采集失败")
                app.diagnostics.add("state", JSONObject().put("state", "error").put("reason", error.message))
                updateNotification()
                if (failures >= 5 || error.message?.contains("更新支持控制协议") == true) {
                    stopForeground(STOP_FOREGROUND_REMOVE)
                    stopSelf()
                    return
                }
                delay((1000L shl (failures - 1)).coerceAtMost(16_000))
            }
        }
    }

    private suspend fun stopSession() {
        app.diagnostics.add("state", JSONObject().put("state", "stopping"))
        stopping = true
        accepting = false
        session?.cancelAndJoin()
        insightJob?.cancel()
        repository.state(repository.snapshot.value.connection, AcquisitionState.STOPPING)
        try {
            transport.stopAcquisition()
            repository.state(ConnectionState.DISCONNECTED, AcquisitionState.STOPPED)
        } catch (error: Exception) {
            repository.state(ConnectionState.DISCONNECTED, AcquisitionState.ERROR,
                "手机已停止接收；设备停止未确认：${error.message}")
        } finally {
            transport.disconnect()
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
    }

    private fun notification(): Notification {
        val snapshot = repository.snapshot.value
        val fresh = snapshot.heart.observedAt?.let { java.time.Duration.between(it, java.time.Instant.now()).toMillis() < 3000 } == true
        val hr = if (fresh) snapshot.heart.bpm?.let { "%.0f".format(it) } else null
        val hrv = if (fresh) snapshot.heart.hrvRmssdMs?.let { "%.1f".format(it) } else null
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val stop = PendingIntent.getService(this, 1, Intent(this, javaClass).setAction(STOP), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        return NotificationCompat.Builder(this, CHANNEL).setSmallIcon(R.drawable.ic_collar)
            .setContentTitle("心率 ${hr ?: "--"} BPM · HRV ${hrv ?: "--"} ms")
            .setContentText(snapshot.lastError ?: snapshot.analysisMessage)
            .setContentIntent(open).setOngoing(true).setOnlyAlertOnce(true)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .addAction(R.drawable.ic_collar, "停止采集", stop).build()
    }

    private fun updateNotification() { getSystemService(NotificationManager::class.java).notify(NOTIFICATION, notification()) }
    override fun onBind(intent: Intent?): IBinder? = null
    override fun onDestroy() {
        accepting = false
        scope.cancel()
        // No automatic restart: Activity launch or an explicit Settings action owns it.
        runBlocking { transport.disconnect() }
        if (repository.snapshot.value.acquisition !in listOf(AcquisitionState.STOPPED, AcquisitionState.ERROR)) {
            repository.state(ConnectionState.DISCONNECTED, AcquisitionState.ERROR, "采集服务已结束；设备停止状态未确认")
        }
        super.onDestroy()
    }
}
