# ESP32-C3 传感器与 Hi ESP 统一固件合并报告

日期：2026-07-25

分支：`feat/unified-sensor-mic-firmware`

目标版本：`0.2.0`

## 1. 合并结论

本次合并已经把 ECG、IMU、OLED、Hi ESP 本地唤醒、MIC1 ADPCM 音频、PC
侧 ASR/VAD、Health MCP 和 ordinary Webhook 主链路收敛到 ESP32-C3 的默认
固件和主 PC GUI 中。

生产设备只使用一个加密 BLE GATT 连接和一个 TX characteristic。V0 传感器
帧与 MIC1 帧保持原 wire format，由固件端优先级队列和 PC 端统一 demux
串行复用。历史 classic ESP32/SPP 和 `-Voice` 设备端 Wi-Fi/Volc ASR 路径不再
属于活跃构建。

软件构建与自动化验证已经通过。没有执行刷写、串口 monitor、人体电极采集
或硬件长时间台架测试。

## 2. 固件架构

### 2.1 单核调度

| 任务 | 优先级 | 实现结果 |
|---|---:|---|
| ECG GPTimer/ADC oneshot | 11 | 保持 500 Hz 原始 ADC 主流 |
| INMP441 I2S capture | 9 | 只读 DMA、PCM16 转换、无阻塞投递 |
| IMU sampling | 8 | 保持 50 Hz |
| V0 packet assembly | 7 | ECG/IMU/status 打包不变 |
| BLE TX arbiter | 6 | V0 高优先级、MIC1 低优先级 |
| WakeNet/ADPCM worker | 5 | Hi ESP 检测或音频压缩 |
| OLED | 3 | 低频轮换显示传感器与 MIC 状态 |

`mic_runtime` 使用四个固定 400-sample PCM block。I2S capture 不等待
WakeNet、ADPCM、BLE 或日志。工作线程落后时丢弃麦克风 block 并增加 I2S
错误计数，不阻塞 ECG/IMU。

### 2.2 Hi ESP 与 MIC1

- 官方模型：`wn9s_hiesp`。
- 输入：INMP441，16 kHz，32-bit I2S slot，内部 PCM16。
- 统一固件编码：IMA-ADPCM。
- 音频帧：400 samples / 25 ms，235 bytes。
- 待机时只运行 WakeNet，不发送持续音频。
- 唤醒后暂停 WakeNet并发送 MIC1 wake/status/audio。
- PC `MIC STOP`、断线或 30 秒上限结束本次流。
- 连接仍有效且未 DISARM 时自动回到 ARMED。
- 麦克风或 WakeNet 初始化失败只把 MIC 置为 ERROR，传感器继续启动。

MIC1 C golden self-test 在启动时执行。公共运行时 API 放在
`neckband_interfaces`，实现保留在独立 `mic_runtime` 组件中。

### 2.3 BLE 仲裁

- transport item 上限从 V0 包大小扩展到 256 bytes。
- V0 使用 32 项高优先级队列。
- MIC1 使用 8 项低优先级队列。
- TX task 在发送低优先级帧前重新检查高优先级队列。
- 被高优先级帧抢占的低优先级帧保存在 TX task 本地，不回队竞争。
- 低优先级队列满时拒绝最新 MIC1 帧，`mic_runtime` 增加 `tx_errors`。
- 低优先级拥塞不增加传感器 `queue_overflow_count`。
- 断线时清空两级队列和本地待发低优先级帧，重连不重放旧音频。
- 连接后请求 7.5-15 ms interval，并在 MIC1 status 中报告实际 interval units。

### 2.4 控制与状态机

```text
DISCONNECTED
-> BLE encrypted notifications subscribed
-> ARMED
-> Hi ESP detected
-> STREAMING
-> MIC STOP / disconnect / 30 s timeout
-> ARMED or DISCONNECTED
```

同一 BLE RX characteristic 按内容路由：

- V0 magic `SN`：交给现有 V0 ACK/control decoder。
- `MIC ARM`：启用并恢复自动待机。
- `MIC DISARM`：关闭 WakeNet 待机和音频发送。
- `MIC STOP`：结束本次音频并自动重新待机。
- `MIC SHIFT <10..20>`：严格十进制范围校验。
- 其他输入：拒绝并增加 MIC control error。

## 3. PC 应用与协议

### 3.1 单一 BLE owner

`BlePacketReader` 是生产路径唯一 BLE owner。原始 notification chunk 在 demux
前写入 raw recorder，之后由 `UnifiedStreamDemux` 识别 V0 `SN` 或 MIC1
`MIC1` magic。

demux 支持：

- 任意 ATT 分片；
- 一个 chunk 内多个连续帧；
- V0/MIC1 混合顺序；
- 部分 magic；
- 随机噪声；
- payload 内伪 magic；
- 长度、版本和 CRC 错误恢复；
- 断线后的 parser/demux reset。

V0 与 MIC1 CRC 分开统计。V0 坏帧继续计入原有传感器
`ParserStats.crc_errors`，MIC1 坏帧不会污染 ECG 统计。

### 3.2 主 GUI

主 GUI 新增 `Microphone / Hi ESP` 面板，包含：

- ARM、DISARM、STOP；
- PCM shift 10..20；
- ARMED/STREAMING/ERROR 状态；
- wake、I2S、TX、clip、sequence gap 和 connection interval；
- MIC1 波形；
- 现有火山 V3 流式 ASR；
- 现有 threshold VAD；
- VAD speech-end 自动发送 `MIC STOP`；
- ASR final 使用稳定 `mic-<32 lowercase hex>` instruction ID；
- ordinary Webhook SQLite/dispatcher 幂等提交。

PC 成功完成加密 notification subscription 后自动发送 `MIC ARM`。独立
`pc-mic` 只服务 `CollarMic-*` 实验固件；统一生产设备使用 `CollarC3-*`
和主 GUI。

## 4. 构建与分区

默认命令：

```powershell
.\tools\project.ps1 build -Target esp32c3
.\tools\project.ps1 size -Target esp32c3
```

默认输出：`firmware/build-c3-unified`。

回滚诊断命令：

```powershell
.\tools\project.ps1 build -Target esp32c3 -SensorsOnly
.\tools\project.ps1 size -Target esp32c3 -SensorsOnly
```

诊断输出：`firmware/build-c3-sensors-only`。

统一分区布局：

| 分区 | Offset | 大小 |
|---|---:|---:|
| `nvs` | `0x009000` | 24 KiB |
| `phy_init` | `0x00F000` | 4 KiB |
| `factory` | `0x010000` | 2 MiB |
| `model` | `0x210000` | 256 KiB |
| `storage` | `0x250000` | 1728 KiB |

该布局与旧固件不同，首次部署必须完整写入 bootloader、partition table、app
和 model。构建不会自动刷写。

## 5. 删除和替换的旧路径

本次删除了活跃的历史设备端语音实现：

- `firmware/components/voice_deps/`；
- `firmware/partitions_voice.csv`；
- `firmware/sdkconfig.defaults.voice`；
- `tools/voice-provision.ps1`；
- `tools/voice-model-provision.ps1`；
- 设备端 Wi-Fi 配网、WebSocket 和 Volc ASR 运行时依赖。

默认目标只接受 `esp32c3`。classic ESP32、ESP-WROOM-32 和 Bluetooth Classic
SPP 不再有活跃固件目标或生产后端。

## 6. 验证结果

### 6.1 固件

| 构建 | `.bin` | App 余量 | 静态 DRAM | 结果 |
|---|---:|---:|---:|---|
| Unified | 673,808 B | 68% | 132,707 B / 41.30% | PASS |
| SensorsOnly | 543,904 B | 74% | 121,269 B / 37.74% | PASS |
| INMP441 diagnostic | 624,768 B | 60% | 30.68% | PASS |

Build gates：

- Unified app 小于 2 MiB：PASS。
- App 分区余量至少 20%：PASS，实际 68%。
- 静态 DRAM 不超过 65%：PASS，实际 41.30%。
- `wn9s_hiesp` 模型存在：PASS。
- `srmodels/srmodels.bin`：125,943 B，不是 4-byte placeholder。
- 固件版本：`0.2.0`。

### 6.2 PC 与静态检查

| 检查 | 结果 |
|---|---|
| 完整 PC pytest | 195 passed |
| CRC 统计定向回归 | 10 passed |
| Python `compileall` | PASS |
| PowerShell parser | PASS |
| 主 GUI offscreen 构造/关闭 | PASS |
| `git diff --check` | PASS |

完整测试曾发现 BLE demux 提前丢弃 V0 CRC 坏帧后没有同步原
`ParserStats.crc_errors`。该回归已修复并新增 V0/MIC1 分离统计测试。

## 7. 本地运行

工作目录：

```powershell
Set-Location 'C:\Users\XWen1024\Documents\smart-neckband\data\worktrees\unified-integration'
```

首次或依赖变化后：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\project.ps1 pc-setup
```

启动主 GUI：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\project.ps1 pc-gui
```

在 GUI 中选择 BLE，扫描并连接 `CollarC3-*`。订阅成功后设备应自动进入
ARMED；说出 Hi ESP 后进入 STREAMING，VAD 结束后回到 ARMED。

## 8. 本地构建与刷写

最快可靠的本机 ESP-IDF 环境：

```powershell
. 'C:\Espressif\tools\Microsoft.v6.0.2.PowerShell_profile.ps1'
Set-Location 'C:\Users\XWen1024\Documents\smart-neckband\data\worktrees\unified-integration'
.\tools\project.ps1 build -Target esp32c3
.\tools\project.ps1 size -Target esp32c3
```

确认 `config/local.ps1` 中 `$ProjectSerialPort = "COM21"` 后，完整刷写统一
固件的命令是：

```powershell
.\tools\project.ps1 flash -Target esp32c3
```

该 `flash` 会使用统一构建目录的 bootloader、partition table、app 和模型
flash arguments。当前任务没有执行这条命令。不要在人体电极连接时使用 USB
刷写或串口调试。

## 9. 尚未验证的硬件门槛

以下项目必须在无人体电极、电池/安全台架和 COM21 条件下另行完成：

1. Hi ESP 待机 30 分钟，ECG missed notification、ECG ring overflow、IMU
   overflow 和 watchdog reset 均为 0。
2. 连续 10 分钟混合传输，ECG 500 Hz、IMU 50 Hz 完整且 MIC1 sequence
   gaps 为 0。
3. 每分钟至少一次 wake，验证 ASR、VAD STOP 和自动 re-arm。
4. 模拟 BLE 拥塞，只允许 MIC1 `tx_errors` 增加。
5. 断线重连后传感器恢复、MIC 自动待机且旧音频不重放。
6. OLED、Health MCP、ordinary Webhook 和真实 ASR final 联合验证。
7. 启动 WakeNet 后 minimum free heap >= 40 KiB；连续语音时 >= 32 KiB。

在这些台架门槛通过前，不应声称实时性、长时间稳定性或人体 ECG 已经完成
硬件验证。
