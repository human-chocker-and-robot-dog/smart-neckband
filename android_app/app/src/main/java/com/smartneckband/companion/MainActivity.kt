package com.smartneckband.companion

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothManager
import android.bluetooth.le.*
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.ParcelUuid
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.*
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.smartneckband.companion.data.*
import com.smartneckband.companion.service.CollarAcquisitionService
import com.smartneckband.companion.ui.CompanionUi
import kotlinx.coroutines.*

data class NearbyCollar(val address: String, val name: String, val rssi: Int)

class MainActivity : ComponentActivity() {
    private val app get() = application as SmartCollarApplication
    private var nearby by mutableStateOf<List<NearbyCollar>>(emptyList())
    private var scanning by mutableStateOf(false)
    private var notice by mutableStateOf<String?>(null)
    private var deviceName by mutableStateOf("")
    private var scanner: BluetoothLeScanner? = null
    private var scanJob: Job? = null
    private var permissionAction: (() -> Unit)? = null
    private val requestPermissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        if (hasBlePermissions()) permissionAction?.invoke() else notice = "需要附近设备权限才能连接颈环，可在系统应用设置中允许。"
        permissionAction = null
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            notice = "通知权限未开启，后台采集通知可能不会出现在通知栏。"
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        app.launchPolicy.attachTask(taskId)
        enableEdgeToEdge()
        deviceName = app.preferences.getString("device_name", "尚未选择设备").orEmpty()
        setContent {
            CompanionUi(app, deviceName, nearby, scanning, notice,
                onScan = { withPermissions { scan() } },
                onSelect = { select(it) }, onStart = { withPermissions { start() } },
                onStop = { stop() })
        }
        if (!app.preferences.getString("device_address", null).isNullOrBlank()) {
            withPermissions {
                if (app.launchPolicy.autoStart(true, hasBlePermissions())) start()
            }
        }
    }

    private fun hasBlePermissions(): Boolean = blePermissions().all {
        ContextCompat.checkSelfPermission(this, it) == PackageManager.PERMISSION_GRANTED
    }
    private fun blePermissions() = if (Build.VERSION.SDK_INT >= 31)
        listOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
    else listOf(Manifest.permission.ACCESS_FINE_LOCATION)

    private fun withPermissions(action: () -> Unit) {
        val required = blePermissions() + if (Build.VERSION.SDK_INT >= 33) listOf(Manifest.permission.POST_NOTIFICATIONS) else emptyList()
        if (required.all { ContextCompat.checkSelfPermission(this, it) == PackageManager.PERMISSION_GRANTED }) action()
        else { permissionAction = action; requestPermissions.launch(required.toTypedArray()) }
    }

    @SuppressLint("MissingPermission")
    private val callback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            runOnUiThread {
                val item = NearbyCollar(result.device.address, result.device.name ?: "Smart Collar", result.rssi)
                nearby = (nearby.filterNot { it.address == item.address } + item).sortedByDescending { it.rssi }
            }
        }
        override fun onScanFailed(errorCode: Int) { runOnUiThread { scanning = false; notice = "扫描失败：$errorCode" } }
    }

    @SuppressLint("MissingPermission")
    private fun scan() {
        if (!hasBlePermissions()) return
        stopScan()
        nearby = emptyList()
        notice = null
        val adapter = getSystemService(BluetoothManager::class.java)?.adapter
        if (adapter?.isEnabled != true) { notice = "请先开启手机蓝牙。"; return }
        scanner = adapter.bluetoothLeScanner
        scanning = true
        scanner?.startScan(listOf(ScanFilter.Builder().setServiceUuid(ParcelUuid(BleCollarTransport.SERVICE)).build()),
            ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(), callback)
        scanJob = lifecycleScope.launch { delay(10_000); stopScan(); if (nearby.isEmpty()) notice = "未发现颈环，请确认设备已开机且未被其他上位机占用。" }
    }

    @SuppressLint("MissingPermission")
    private fun stopScan() {
        scanJob?.cancel()
        if (hasBlePermissions()) scanner?.stopScan(callback)
        scanner = null
        scanning = false
    }

    private fun select(device: NearbyCollar) {
        stopScan()
        app.preferences.edit().putString("device_address", device.address).putString("device_name", device.name).apply()
        deviceName = device.name
        start()
    }
    private fun start() {
        if (app.preferences.getString("device_address", null).isNullOrBlank()) { notice = "请先扫描并选择颈环。"; return }
        notice = null
        app.launchPolicy.manualStart()
        ContextCompat.startForegroundService(this, Intent(this, CollarAcquisitionService::class.java).setAction(CollarAcquisitionService.START))
    }
    private fun stop() {
        app.launchPolicy.stop()
        startService(Intent(this, CollarAcquisitionService::class.java).setAction(CollarAcquisitionService.STOP))
    }
    override fun onStop() { stopScan(); super.onStop() }
    override fun onDestroy() {
        if (isFinishing) app.launchPolicy.finishTask(taskId)
        super.onDestroy()
    }
}
