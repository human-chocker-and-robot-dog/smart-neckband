package com.smartneckband.companion.data

import android.annotation.SuppressLint
import android.bluetooth.*
import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import com.smartneckband.companion.protocol.*
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.UUID

/** GATT operations are serialized; only a matching device ACK completes control. */
@SuppressLint("MissingPermission") // Entry points are guarded by Activity/service permission checks.
class BleCollarTransport(
    private val context: Context,
    private val onFrame: (V0Frame, ParserStats) -> Unit,
    private val onDisconnected: (String) -> Unit,
    private val onBytes: (ByteArray) -> Unit = {},
) : CollarTransport {
    private var gatt: BluetoothGatt? = null
    private var rx: BluetoothGattCharacteristic? = null
    private val parser = V0StreamParser()
    private var ready = CompletableDeferred<Unit>()
    private var written: CompletableDeferred<Unit>? = null
    private var ack: Pair<Long, CompletableDeferred<V0Frame.ControlAck>>? = null
    private val operations = Mutex()
    private var requestId = 0L

    companion object {
        val SERVICE: UUID = UUID.fromString("6e400001-b5a3-f393-e0a9-e50e24dcca9e")
        val RX: UUID = UUID.fromString("6e400002-b5a3-f393-e0a9-e50e24dcca9e")
        val TX: UUID = UUID.fromString("6e400003-b5a3-f393-e0a9-e50e24dcca9e")
        val CCCD: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
    }

    private fun fail(reason: String) {
        val error = IllegalStateException(reason)
        ready.completeExceptionally(error)
        written?.completeExceptionally(error)
        ack?.second?.completeExceptionally(error)
        onDisconnected(reason)
    }

    private val callback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(client: BluetoothGatt, status: Int, newState: Int) {
            if (client !== gatt) return
            if (status != BluetoothGatt.GATT_SUCCESS || newState == BluetoothProfile.STATE_DISCONNECTED) {
                fail("蓝牙连接中断（$status）")
            } else if (newState == BluetoothProfile.STATE_CONNECTED) {
                // 20-byte chunks remain valid even if MTU negotiation is unavailable.
                if (!client.discoverServices()) fail("无法发现设备服务")
            }
        }

        override fun onServicesDiscovered(client: BluetoothGatt, status: Int) {
            if (client !== gatt) return
            if (status != BluetoothGatt.GATT_SUCCESS) { fail("服务发现失败：$status"); return }
            val service = client.getService(SERVICE)
            rx = service?.getCharacteristic(RX)
            val tx = service?.getCharacteristic(TX)
            val cccd = tx?.getDescriptor(CCCD)
            if (rx == null || tx == null || cccd == null) { fail("设备不支持颈环 BLE 协议"); return }
            if (!client.setCharacteristicNotification(tx, true)) { fail("无法订阅数据"); return }
            val started = if (Build.VERSION.SDK_INT >= 33) {
                client.writeDescriptor(cccd, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE) == BluetoothStatusCodes.SUCCESS
            } else {
                @Suppress("DEPRECATION")
                cccd.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                @Suppress("DEPRECATION")
                client.writeDescriptor(cccd)
            }
            if (!started) fail("无法启用数据通知")
        }

        override fun onDescriptorWrite(client: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) {
            if (client !== gatt || descriptor.uuid != CCCD) return
            if (status == BluetoothGatt.GATT_SUCCESS) ready.complete(Unit) else fail("订阅失败，请检查配对：$status")
        }

        override fun onCharacteristicWrite(client: BluetoothGatt, characteristic: BluetoothGattCharacteristic, status: Int) {
            if (client !== gatt || characteristic.uuid != RX) return
            if (status == BluetoothGatt.GATT_SUCCESS) written?.complete(Unit) else written?.completeExceptionally(IllegalStateException("控制写入失败：$status"))
        }

        override fun onCharacteristicChanged(client: BluetoothGatt, characteristic: BluetoothGattCharacteristic, value: ByteArray) {
            if (client === gatt && characteristic.uuid == TX) receive(value)
        }

        @Deprecated("Pre Android 13 callback")
        override fun onCharacteristicChanged(client: BluetoothGatt, characteristic: BluetoothGattCharacteristic) {
            if (Build.VERSION.SDK_INT < 33 && client === gatt && characteristic.uuid == TX) {
                @Suppress("DEPRECATION")
                receive(characteristic.value.copyOf())
            }
        }
    }

    @Synchronized private fun receive(bytes: ByteArray) {
        onBytes(bytes)
        parser.feed(bytes).forEach { frame ->
            if (frame is V0Frame.ControlAck && ack?.first == frame.requestId) ack?.second?.complete(frame)
            onFrame(frame, parser.stats)
        }
    }

    override suspend fun connect(deviceId: String) = withContext(Dispatchers.Main.immediate) {
        disconnect()
        val adapter = context.getSystemService(BluetoothManager::class.java)?.adapter
            ?: error("手机不支持蓝牙")
        check(adapter.isEnabled) { "请先开启蓝牙" }
        val device = adapter.getRemoteDevice(deviceId)
        if (device.bondState != BluetoothDevice.BOND_BONDED) {
            check(device.bondState == BluetoothDevice.BOND_BONDING || device.createBond()) { "无法发起安全配对" }
            withTimeout(60_000) {
                while (device.bondState != BluetoothDevice.BOND_BONDED) { delay(500) }
            }
        }
        ready = CompletableDeferred()
        parser.reset()
        // Keep GATT state, parser reset and callbacks on the service's main dispatcher.
        gatt = device.connectGatt(context, false, callback, BluetoothDevice.TRANSPORT_LE,
            BluetoothDevice.PHY_LE_1M_MASK, Handler(Looper.getMainLooper()))
        try { withTimeout(20_000) { ready.await() } } catch (error: Exception) { disconnect(); throw error }
    }

    private suspend fun control(active: Boolean) = operations.withLock {
        val client = gatt ?: error("设备未连接")
        val characteristic = rx ?: error("设备控制服务未就绪")
        val id = ++requestId
        val reply = CompletableDeferred<V0Frame.ControlAck>()
        ack = id to reply
        try {
            withTimeout(5_000) {
                // Conservative ATT payload works at the mandatory 23-byte MTU.
                acquisitionCommand(id, if (active) 1 else 0).asList().chunked(20).forEach { part ->
                    val completion = CompletableDeferred<Unit>()
                    written = completion
                    val started = withContext(Dispatchers.Main.immediate) {
                        if (Build.VERSION.SDK_INT >= 33) {
                            client.writeCharacteristic(characteristic, part.toByteArray(), BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT) == BluetoothStatusCodes.SUCCESS
                        } else {
                            @Suppress("DEPRECATION")
                            characteristic.value = part.toByteArray()
                            characteristic.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
                            @Suppress("DEPRECATION")
                            client.writeCharacteristic(characteristic)
                        }
                    }
                    check(started) { "控制写入未启动" }
                    completion.await()
                }
                val response = reply.await()
                check(response.result == 0 && response.active == active) { "设备未确认${if (active) "开始" else "停止"}采集" }
            }
        } catch (error: TimeoutCancellationException) {
            throw IllegalStateException("设备未确认采集控制；请更新支持控制协议的固件", error)
        } finally { ack = null; written = null }
    }

    override suspend fun startAcquisition() { control(true) }
    override suspend fun stopAcquisition() { control(false) }
    override suspend fun disconnect() = withContext(Dispatchers.Main.immediate) {
        val old = gatt
        gatt = null
        rx = null
        old?.disconnect()
        old?.close()
        Unit
    }
}
