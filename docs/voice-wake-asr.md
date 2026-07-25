# “主人主人”离线唤醒与火山流式 ASR

## 链路与边界

Voice 构建只面向 ESP32-C3：

```text
INMP441 -> ESP-SR WakeNet9s“主人主人” -> 火山流式 ASR
         -> V0 BLE final 文本 -> PC SQLite -> Agent Webhook
```

WakeNet 在设备本地连续运行；未唤醒时不会连接 ASR，也不会上传音频。唤醒后，设备发送最多 1 秒 PCM16 预卷和后续语音。音频不会写日志、不会存入 Flash、不会通过 BLE 发给上位机。只有非空 ASR final 文本会进入 BLE 的低优先级可靠队列；ECG 原始流仍使用高优先级队列。

## INMP441 候选接线

| INMP441 | ESP32-C3 SuperMini | 说明 |
|---|---:|---|
| `VDD` | `3V3` | 禁止接 5 V |
| `GND` | `GND` | 与传感器共地 |
| `SCK/BCLK` | GPIO4 | I2S bit clock |
| `WS/LRCL` | GPIO5 | I2S word select，仅作数字信号 |
| `SD` | GPIO20 | I2S data input |
| `L/R` | `GND` | 固件读取 left slot |

这是候选映射。必须先确认手中 SuperMini 克隆板确实引出 GPIO20，且 GPIO4/5/20 没有板载冲突。未完成实板核验前不要焊死或用于人体连接。

音频格式固定为 16 kHz、单声道、32-bit I2S slot，固件右移 14 bit 并饱和转换成 PCM16。每个运行帧为 100 ms（1600 个采样）；1 秒预卷占 32 KiB RAM。

## 构建

```powershell
. 'C:\Espressif\tools\Microsoft.v6.0.2.PowerShell_profile.ps1'
.\tools\project.ps1 build -Target esp32c3 -Voice
.\tools\project.ps1 size -Target esp32c3 -Voice
```

Voice 使用独立的 `build-esp32c3-voice`、`sdkconfig.esp32c3-voice` 和 4 MiB 分区表。普通 ESP32-C3 构建不启用语音源码。

Voice 分区包含 2 MiB factory app、1 MiB `model`、24 KiB `voicecfg` 和剩余 `storage`。把该布局写入已有设备属于分区变更，必须另行获得明确授权；本计划没有自动烧录、擦除或打开串口监视器。

托管依赖固定为 ESP-SR 2.4.6、`esp_websocket_client` 1.7.0 和 cJSON 1.7.19~2，版本记录在 `firmware/dependencies.lock`。

## Gate 0：精确自定义模型

ESP-SR 自带的 C3 WakeNet9s 词表不包含“主人主人”。仓库不会用“你好小智”或其他内置词代替。

当前 `firmware/models/wakenet_manifest.json` 的状态是 `required_not_supplied`。Voice 镜像可以在没有模型时编译，但启动后保持 `DISABLED / MODEL`，不会启动麦克风或联网。运行时只接受 `model` 分区中 wake-word 元数据严格等于 UTF-8“主人主人”的模型。

取得 Espressif 官方自定义 WakeNet9s 的 `srmodels.bin` 分区镜像后：

1. 核对目标为 ESP32-C3、词为“主人主人”、产物不超过 1 MiB。
2. 把许可证标识和 SHA-256 写入 manifest，并把 `model_status` 改成 `ready`。
3. 在没有人体电极、充电器和接地仪器连接时，另行授权执行：

```powershell
.\tools\project.ps1 voice-model-provision `
    -Target esp32c3 `
    -Voice `
    -WakeNetModelPath C:\secure\srmodels.bin
```

工具会再次校验目标、词、状态、许可证、大小和 SHA-256，只写 `model` 分区，并要求键入精确安全确认。

## Wi‑Fi 与火山凭据

Voice 配置放在单独的 `voicecfg` NVS 分区。配置工具支持：

- `api_key`：发送 `X-Api-Key`；
- `legacy`：发送 `X-Api-App-Key` 与 `X-Api-Access-Key`。

两者都发送 `X-Api-Resource-Id` 和每轮随机 `X-Api-Connect-Id`。入口固定为：

```text
wss://openspeech.bytedance.com/api/v3/sauc/bigmodel
```

授权后执行：

```powershell
.\tools\project.ps1 voice-provision -Target esp32c3 -Voice
```

工具用隐藏输入读取 Wi‑Fi 密码和密钥，在随机临时目录生成 24 KiB NVS 镜像，只写 `voicecfg`，随后清除临时文件。未启用 Flash Encryption 时，普通 NVS 不具备硬件级秘密保护；丢失设备可能暴露凭据。

## ASR 会话

- Wi‑Fi 在 Voice 运行期间保持连接；WebSocket 只在唤醒后建立。
- 请求音频为 PCM16 little-endian、16 kHz、单声道、100 ms/帧。
- 火山 V3 二进制协议使用官方允许的“无压缩”模式，避免 C3 为每帧分配 gzip 工作内存。
- 服务端参数启用标点、ITN、utterances，`end_window_size=800`、`force_to_speech_time=1000`。
- 本地在至少 1 秒后遇到连续 800 ms 低能量，或总录音达到 15 秒时发送最后一帧。
- 最后结果等待上限为 5 秒；分片、错误帧、畸形长度、超时和音频队列溢出只终止本轮，传感器链路继续运行。
- partial 结果不进入 BLE；非空 final 最长 512 UTF-8 bytes。

设备最多保留 4 条待 PC ACK 的 final 文本，每 2 秒重发一次。PC 先把稳定 instruction ID `voice-<utterance_id>` 持久化到 SQLite，成功后才 ACK，并使用现有 Webhook dispatcher 自动转发给 Agent。

## 当前 PC-side ASR/VAD 测试链路

当前仓库的独立麦克风测试固件只负责 Hi ESP 唤醒和通过 BLE 传输 INMP441 音频；火山流式 ASR 与音频阈值自动断句均在上位机 `smart-neckband-mic` 中运行。该测试入口与生产传感器固件并存，不会替换 ECG/IMU 固件。

上位机 ASR 页现在暴露以下调试参数：

- 火山 ASR：endpoint、Resource ID、鉴权模式、密钥、UID、模型、音频队列深度、WebSocket 发送分片、连接/接收/final 超时、`end_window_size`、`force_to_speech_time`。
- 音频阈值 VAD：采样率固定 16 kHz；界面提供分析窗口、音频队列深度、静默 RMS、说话 RMS、RMS 倍数、最小 RMS 增量、停止静默时长、最小说话时长和采样时长。说话开始阈值依据静默/说话标定计算；说话结束阈值回到静默环境 RMS 加最小增量，并要求连续 1000 ms。当前没有最大录音硬截断。

ASR 默认使用 100 ms 音频分片和 20 ms 接收轮询上限。发送循环会先快速 drain 已缓存音频并连续发出多个 WebSocket audio frame，然后再短暂读取服务端消息；VAD 停止后会扩大 drain 时间片，尽快发送尾部音频和 final 标记，避免队列被 `recv` 超时节流。

运行日志写入 `pc_app/data/mic_capture_debug.log`，包含唤醒、ASR 连接/发送/final、队列丢弃、阈值 VAD chunk RMS/Peak、静默环境采样、说话声音采样、保存采样和异常 traceback。日志会脱敏字段名里包含 key/token/secret/password/authorization 的值。

ASR 和 VAD 队列满时不再把本轮直接打成错误；上位机会丢弃最旧音频帧并继续处理，停止录音时也会优先腾出空间发送 final 标记。音频阈值 VAD 不依赖 FunASR、PyTorch 或本地模型；点击“静默环境采样”或“说话声音采样”后，上位机会直接启动麦克风传输，采样完成后更新界面数值。点击“保存采样”会把静默 RMS 与说话 RMS 写入 `pc_app/data/audio_threshold_vad_settings.json`，下次启动自动加载。

`smart-neckband-mic` 复用现有 Agent Webhook 标签页、SQLite 和 dispatcher。每个 Hi ESP 唤醒会根据 BLE 设备身份、一次连接实例、`detected_sample_index` 和 `wake_count` 生成 `mic-<32-lowercase-hex>` instruction ID。ASR final 先以该 ID 写入普通 Webhook SQLite，再由原有重试 worker 提交；相同 final 重复到达是幂等的，同一 ID 对应不同文本会被拒绝。Agent 最终回复继续由原 callback receiver、SQLite 和 UI 处理。

正常 Hi ESP 识别默认不保存 WAV。只有用户显式勾选“保存唤醒 WAV（诊断）”或运行手动波形测试时才写入本地 `captures/`；该目录和调试日志均被 Git 忽略。

## 状态与错误

`VOICE_STATUS.flags`：

| Bit | 含义 |
|---:|---|
| 0 | INMP441/I2S ready |
| 1 | 精确 WakeNet 模型 ready |
| 2 | Wi‑Fi ready |
| 3 | 当前 ASR WebSocket connected |

`last_error` 的 1–10 依次表示配置、模型、音频、Wi‑Fi、WebSocket、协议、音频队列、final 超时、空 final、BLE 文本队列错误。状态和计数不包含密钥或音频内容。

## 尚未验证

软件构建和协议测试不能替代以下硬件证据：

- GPIO20 在具体克隆板上的可用性；
- INMP441 幅度、削顶、PCM shift 与持续 I2S 运行；
- 官方“主人主人”模型的命中率、误唤醒率和内存占用；
- 真实火山账号鉴权、端到端 final 文本、BLE/Webhook 闭环；
- BLE + Wi‑Fi + WakeNet + ECG 的长时间并发、最小空闲 heap 和 WDT。

这些检查必须在另行授权的无人体电极台架上完成。人体 ECG 只允许独立电池供电并通过无线传输。
