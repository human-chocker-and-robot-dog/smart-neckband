# ESP32-C3 统一传感器与 Hi ESP 语音链路

## 当前产品链路

默认 ESP32-C3 固件同时运行：

- ECG 500 Hz 原始 ADC 采样；
- MPU6050 IMU 50 Hz；
- OLED 低频状态页；
- INMP441 16 kHz 单声道采集；
- ESP-SR WakeNet9s 官方 `wn9s_hiesp` 本地唤醒；
- 加密 BLE GATT 上的 V0 传感器包和 MIC1 IMA-ADPCM 音频。

PC 主界面的同一个 `BlePacketReader` 拥有唯一 BLE 连接。原始 ATT chunk
先写入 raw recorder，再由统一 demux 根据 V0 magic `SN` 或 MIC1 magic
`MIC1` 分流。PC 解码音频、运行火山 V3 流式 ASR 和阈值 VAD，并把 final
文本写入现有 ordinary Webhook SQLite/dispatcher。

设备不连接 Wi-Fi，不保存火山密钥，也不运行设备端 ASR。历史 `-Voice`、
`voicecfg` 和设备端 WebSocket 路径已经删除。

## 运行状态机

```text
DISCONNECTED
-> encrypted BLE notification subscribed
-> ARMED
-> Hi ESP detected
-> STREAMING
-> PC MIC STOP or 30 second timeout
-> ARMED
```

PC 订阅通知后自动发送 `MIC ARM`。VAD 判定语音结束时发送 `MIC STOP`；
固件停止当前音频并自动回到 WakeNet 待机。断线会停止传输，清空旧 BLE
音频队列；重连订阅后重新自动待机。

## 调度与降级

固件任务优先级从高到低为 ECG 11、I2S capture 9、IMU 8、V0 packet 7、
BLE TX 6、WakeNet/ADPCM 5、OLED 3。

I2S 使用四个固定 400-sample PCM block。采集任务只读取 DMA、转换 PCM16
并无阻塞投递；WakeNet、压缩、BLE 和日志不会阻塞 I2S。BLE TX 每次准备
发送低优先级 MIC1 帧前都会重新检查高优先级 V0 队列。

ECG/IMU 完整性高于音频连续性。音频低优先级队列满时丢弃最新 MIC1 帧，
增加 MIC1 `tx_errors`，不增加传感器 transport overflow，也不降低 ECG 或
IMU 采样率。麦克风初始化失败只禁用语音，传感器继续运行。

## 接线

| INMP441 | ESP32-C3 SuperMini |
|---|---:|
| `VDD` | `3V3` |
| `GND` | `GND` |
| `SCK/BCLK` | GPIO4 |
| `WS/LRCL` | GPIO5 |
| `SD` | GPIO20 |
| `L/R` | `GND`，读取 left slot |

音频固定为 16 kHz、32-bit I2S slot、PCM16 内部表示和 MIC1 IMA-ADPCM
无线传输。GPIO4、GPIO5、GPIO20 必须先在具体 SuperMini 克隆板上确认。

## 构建

加载 ESP-IDF v6.0.2 PowerShell profile 后：

```powershell
.\tools\project.ps1 doctor -Target esp32c3
.\tools\project.ps1 build -Target esp32c3
.\tools\project.ps1 size -Target esp32c3
```

默认输出目录是 `firmware/build-c3-unified`。诊断回滚构建：

```powershell
.\tools\project.ps1 build -Target esp32c3 -SensorsOnly
.\tools\project.ps1 size -Target esp32c3 -SensorsOnly
```

独立 INMP441 实验固件仍用于 PCM16/PCM8、I2S 和麦克风硬件诊断：

```powershell
.\tools\inmp441-test.ps1 build
.\tools\inmp441-test.ps1 size
```

## 分区

统一固件使用 4 MiB Flash：

| 分区 | Offset | 大小 |
|---|---:|---:|
| `nvs` | `0x009000` | 24 KiB |
| `phy_init` | `0x00F000` | 4 KiB |
| `factory` | `0x010000` | 2 MiB |
| `model` | `0x210000` | 256 KiB |
| `storage` | `0x250000` | 1728 KiB |

构建会自动把官方 `wn9s_hiesp` 打包进 `model` 分区。该布局与旧固件不同，
现有设备必须完整刷写 bootloader、partition table、app 和 model。刷写、擦除
Flash 或打开串口 monitor 仍需用户明确授权。

## PC 主界面

```powershell
.\tools\project.ps1 pc-setup
.\tools\project.ps1 pc-gui
```

连接 `CollarC3-*` 后，主 GUI 的 `Microphone / Hi ESP` 页显示 ARMED、
STREAMING、I2S/TX/clip、实际 BLE connection interval、MIC1 sequence gap、
音量、波形和 ASR final。该页可手动 ARM、DISARM、STOP，并可调整诊断
`PCM shift` 10..20。

ASR 配置沿用 `data/volc_asr_settings.json` 或环境变量；VAD 配置沿用
`data/audio_threshold_vad_settings.json`。每次 wake 使用稳定
`mic-<32 lowercase hex>` instruction ID，重复 final 对 ordinary Webhook
写入保持幂等。

统一设备使用 `CollarC3-*` 和主 GUI 的 `Microphone / Hi ESP` 页面。旧
`pc-mic` 命令现在只是兼容别名，也会打开同一个主 GUI，不再启动第二套
麦克风上位机。

## 安全与硬件验证

USB 调试只允许无人体电极的电子台架。人体 ECG 必须使用独立电池和纯无线
BLE；电极连接人体时不得同时连接桌面 USB、墙充、正在充电的移动电源或
接地台式仪器。

软件构建和自动化测试不能替代 30 分钟待机、10 分钟混合传输、BLE 拥塞、
重连、OLED、Health MCP、Webhook 和真实 ASR final 的硬件台架验证。
