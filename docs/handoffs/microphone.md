# Smart Collar 麦克风 / 本地唤醒 / PC ASR 合并 Handoff

> 集成注记（2026-07-25）：最终仓库硬件目标统一为 ESP32-C3。下文出现的经典
> ESP32/SPP 仅用于描述历史提交或兼容合同，不再对应活动固件目标。

Last updated: 2026-07-25.

这份文档服务于后续“大合并”。目标是让接手 Agent 直接复用已经完成的
INMP441、ESP32-C3 本地唤醒、BLE 音频、Windows 上位机、火山流式 ASR、
自动断句和调试设施，而不是根据需求重新写一套。

本文档不复制源代码。所有实现入口、测试入口、已验证事实、废弃路线和合并冲突
都直接指向仓库中的文件与提交。

本 handoff 审阅的麦克风分支基线是：

`912a416` 是功能实现基线；本文件会以一个后续 docs-only commit 提交，因此合并时
不要把 handoff 提交本身误认为新的运行时实现。

| 项目 | 值 |
|---|---|
| 分支 | `test/inmp441-ble-capture` |
| 麦克风实现基线 | `912a4166a6cecdfe58cf3e29e0d0bf974fdad0ee` |
| 远程 | `origin/test/inmp441-ble-capture` |
| 主线分叉基线 | `88b841be6f1dc1fdc7f5071769d7a919effbb290` |
| 与 Health MCP 分支的共同祖先 | `abe97defef27bda47b6b1a2163e8908eb1df6d6d` |
| 目标板 | ESP32-C3 SuperMini，4 MB Flash，无 PSRAM |
| 台架端口 | `COM21`，仅作为当前机器事实，不得硬编码 |

## 1. 合并意图

大合并后的目标链路应以当前已经跑通的 PC-side ASR 架构为准：

```text
INMP441
-> ESP32-C3 I2S PCM16
-> ESP-SR WakeNet9s 官方 “Hi ESP” 本地唤醒
-> BLE IMA-ADPCM 音频流
-> Windows PC 解码 PCM16
-> PC 火山引擎 V3 流式 ASR
-> PC 环境音阈值 VAD 自动结束
-> ASR final 文本
-> 复用现有 Agent Webhook durable dispatcher
-> Agent final reply
```

当前分支已经完成到“ASR final 文本显示”。最后一段“自动进入 Agent Webhook”
尚未接线，是大合并必须完成的主要功能缺口。

合并原则：

1. 复用本分支的实验固件、协议解析器、BLE 客户端、ASR 客户端、VAD、日志和测试。
2. 复用主 PC GUI 已有的 Agent Webhook SQLite、重试、回调和去重实现。
3. 复用生产 C3 固件已有的 BLE 加密、传感器采样、队列和板型配置。
4. 不重新实现火山 WebSocket 协议，不重新实现 ADPCM，不重新实现 Webhook HTTP。
5. 不把已经删除的 FunASR/PyTorch VAD 重新引入。
6. 不把早期 ESP-side Wi-Fi ASR 路线误当成当前产品方向。

## 2. 两条语音架构必须分清

仓库里同时存在两条历史路线。合并 Agent 必须先区分，否则很容易把已经放弃的
ESP-side ASR 带回最终固件。

### 2.1 当前选择：Hi ESP + BLE 音频 + PC ASR

来源是 `test/inmp441-ble-capture` 在 `a9c20e5` 之后的连续提交。

- ESP 只运行 INMP441、WakeNet9s 和 BLE 音频流。
- 唤醒词使用 ESP-SR 2.4.6 自带的 `wn9s_hiesp`。
- ASR 凭据、WebSocket 和自动断句全部在 PC。
- 默认音频传输为 IMA-ADPCM，PC 解码回 PCM16。
- 当前用户明确要求 ASR 放在上位机，而不是 ESP32。
- 当前 VAD 不使用模型，以环境音标定和 RMS 为准。

这是大合并的功能方向和运行时事实。

### 2.2 历史路线：主人主人 + ESP Wi-Fi 直连火山 ASR

来源是 `feat/voice-wake-asr` 的 `17fc0df` 和 `abe97de`，实现位于
`firmware/components/voice_deps/`。

这条路线包含：

- ESP-side INMP441 与 1 秒预卷；
- 自定义 WakeNet 模型 Gate；
- ESP Wi-Fi、NVS 凭据和火山 WebSocket；
- ESP-side final 文本队列；
- V0 `VOICE_TEXT_CHUNK / VOICE_STATUS / VOICE_TEXT_ACK`；
- PC durable insert 后 ACK 和 Webhook 转发。

这条路线的软件存在，但精确“主人主人”官方模型从未供应，真实硬件闭环未完成。
用户后来选择官方 `Hi ESP`，并把 ASR 移到 PC。除非用户再次明确改变架构，不要
启用 `voice_runtime.c` 中的 ESP-side ASR，也不要要求 Wi-Fi/火山密钥进入设备。

可以选择性复用的历史模块：

| 模块 | 是否复用 | 原因 |
|---|---|---|
| `firmware/components/voice_deps/voice_audio.*` | 评估后复用 | 已有生产化 I2S/板型边界，但需吸收实验固件的实板参数和超时修复 |
| `firmware/components/voice_deps/voice_wakenet.*` | 评估后复用 | WakeNet 接口可复用，但当前文件严格要求“主人主人”，需改为当前模型策略 |
| `firmware/components/voice_deps/voice_link.*` | 通常不用于 PC-side ASR | 它负责 ESP final 文本可靠发送，而当前 final 文本产生在 PC |
| `firmware/components/voice_deps/voice_runtime.*` | 不复活 | 它拥有 Wi-Fi、ESP WebSocket 和 ESP-side VAD |
| `firmware/components/voice_deps/volc_asr_protocol.*` | 不用于当前运行路径 | PC 已有验证更充分的 `volc_asr_client.py` |
| `firmware/components/voice_deps/voice_config.*` | 当前不需要 | 火山凭据留在 PC；不要写入 ESP Flash |
| V0 voice text/ACK 协议和 `pc_app/.../voice.py` | 保留兼容 | 对未来 ESP 文本或其他可靠文本源有用，但不应绕过 PC Webhook dispatcher |

`docs/voice-wake-asr.md` 同时记录了两条路线，并有部分旧 VAD 参数。合并时以本
handoff、当前提交 `912a416` 和实际源码为准，不要把该文档的旧参数当最终行为。

## 3. 分支与提交历史

### 3.1 共同基础

| 提交 | 作用 |
|---|---|
| `88b841b` | 当前 `main` 基线 |
| `c08e696` | PC Agent Webhook console、SQLite、dispatcher 和 reply receiver |
| `17fc0df` | V0 可靠语音文本分片、状态、ACK 和 PC 重组 |
| `abe97de` | 早期 ESP-side 离线唤醒与流式 ASR 软件基线 |

### 3.2 麦克风分支连续提交

不要只挑最后一个 VAD commit。最安全做法是合并整个分支；如果必须 cherry-pick，
应从共同祖先之后按顺序完整审阅。

| 提交 | 最终保留的价值 |
|---|---|
| `a9c20e5` | 独立 INMP441 BLE 测试固件、MIC1 协议、PC 捕获 GUI、构建工具 |
| `8e42d6c` | IMA-ADPCM、队列、连接参数、丢帧统计、I2S 稳定性修复 |
| `63616c4` | Windows BLE 扫描移出 Qt GUI/STA 线程 |
| `a29d2f8` | ESP-SR 2.4.6、`wn9s_hiesp`、model 分区、ARM/WAKE 状态 |
| `55db5ad` | Hi ESP 静默捕获诊断和固件兼容检测 |
| `1796ea5` | STOP 在 BLE RX 路径立即生效，支持再次 ARM |
| `256c4e8` | PC-side 火山 V3 流式 ASR 和 ASR UI |
| `7da850e` | 曾引入 FunASR VAD；仅作为历史，不应恢复 |
| `0c88458` | 火山 ASR 设置手动保存为本地 JSON |
| `22d0064` | VAD 队列满时丢弃旧帧而不是终止 |
| `b56d52f` | ASR/VAD 队列和异常诊断日志 |
| `9c38577` | 删除 FunASR/PyTorch，改为轻量音频阈值 VAD |
| `3b92ad0` | ASR 快速 drain，解除 `recv()` 对发送队列的节流 |
| `937796b` | 静默/说话采样和本地保存 |
| `6683bca` | 去掉最大录音硬截断，加入启动/结束滞回历史修复 |
| `e1814c5` | ARM 等待 3 秒、ARM 超时不再向失联设备发送 STOP、补 BLE 日志 |
| `912a416` | 当前 VAD：固定 16 kHz，结束条件回归环境音，持续 1000 ms |

FunASR 相关提交是一次已回滚实验。最终 `pc_app/pyproject.toml` 不依赖
`funasr`、`torch` 或 `torchaudio`。

## 4. 硬件事实与接线

实验板是 ESP32-C3 SuperMini，芯片实测为 ESP32-C3 AZ rev 1.1，内置 XMC
4 MB Flash，原生 USB Serial/JTAG，当前台架端口为 COM21。

INMP441 已实际使用以下接线完成录音、WakeNet 和 BLE ASR 测试：

| INMP441 | ESP32-C3 | 约束 |
|---|---:|---|
| `VDD` | `3V3` | 只允许 3.3 V |
| `GND` | `GND` | 共地 |
| `SCK/BCLK` | GPIO4 | I2S bit clock |
| `WS/LRCL` | GPIO5 | I2S word select |
| `SD/DOUT` | GPIO20 | I2S input |
| `L/R` | GND | 选择 left slot |

音频硬件格式：

- 固定 16 kHz、单声道；
- I2S 标准 Philips、32-bit slot；
- 转换为有符号 PCM16；
- 当前实验固件默认右移 16 bit，可通过 `SHIFT 10..20` 台架调试；
- 每个 BLE 音频块 400 样本，即 25 ms。

`docs/hardware/esp32c3-supermini-wiring.md` 仍把这组引脚描述为候选映射。
麦克风实验已经证明 GPIO4/5/20 在当前这块 SuperMini 上可工作，但“麦克风与
ECG、IMU、OLED 同时运行”的整机引脚和资源组合仍未验证。

## 5. 实验固件：直接复用入口

完整固件位于：

| 文件 | 作用 |
|---|---|
| `experiments/inmp441_ble_capture/firmware/main/mic_capture_main.c` | I2S、PCM 转换、ADPCM、WakeNet、命令、MIC1 帧和任务 |
| `experiments/inmp441_ble_capture/firmware/main/idf_component.yml` | 固定 ESP-SR 2.4.6 |
| `experiments/inmp441_ble_capture/firmware/partitions.csv` | 1.5 MiB app + 640 KiB model |
| `experiments/inmp441_ble_capture/firmware/sdkconfig.defaults` | C3、4 MB、NimBLE、MTU、Hi ESP 模型选择 |
| `experiments/inmp441_ble_capture/firmware/dependencies.lock` | 依赖锁定 |
| `tools/inmp441-test.ps1` | 独立 build/size/flash 入口 |

实验固件通过仓库已有 BLE UART 组件提供 Nordic UART 风格 GATT UUID，广播名为
`CollarMic-XXXX`。当前实板为 `CollarMic-2E4A`。

必须保留的固件行为：

1. `ARM` 时只在本地运行 WakeNet，不上传音频。
2. 检测到 Hi ESP 后先发 WAKE 帧，再开始所选音频流。
3. `STOP` 在 BLE RX 回调中立即清除 streaming/armed，并清空旧 TX 队列。
4. 命令任务随后发送状态，确保第二轮无需断电即可重新 ARM。
5. 断开 BLE 后自动停止流。
6. 音频生产与 BLE 消费使用有界队列，错误计数进入状态帧。
7. 默认使用 IMA-ADPCM；PCM8/PCM16 仅保留为带宽诊断模式。

实验固件当前配置 `.encrypted = false`，这是台架便利行为。合并到生产固件时必须
复用 `firmware/main/ble_transport.c` 和仓库 BLE UART 的加密/绑定策略，不要把
实验固件的明文 GATT 配置带入整机。

## 6. MIC1 音频协议

协议实现位于：

- 固件编码：`experiments/inmp441_ble_capture/firmware/main/mic_capture_main.c`
- PC 解码：`pc_app/src/smart_neckband/mic_capture_protocol.py`
- 自动化测试：`pc_app/tests/test_mic_capture_protocol.py`

协议事实：

| 项目 | 当前值 |
|---|---|
| Magic | `MIC1` |
| Version | 1 |
| 帧类型 | AUDIO=1、STATUS=2、WAKE=3 |
| 编码 | PCM16=1、PCM8=2、IMA-ADPCM=3 |
| 校验 | CRC16-CCITT-FALSE |
| 音频采样率 | 16000 Hz，解析器拒绝其他值 |
| 音频序列 | uint32，PC 统计 sequence gap |
| 时间轴 | uint64 first/detected sample index |
| flags | clipped、I2S error、TX error |

每个 IMA-ADPCM 块独立可解码，400 个采样的帧可放入 247-byte ATT MTU。
PC 解析器支持任意 notification 切分、magic 重同步、CRC 错误、畸形帧、丢帧统计、
PCM8/PCM16/ADPCM 解码和 WAV 输出。

ASCII 控制命令为：

- `ARM PCM16 / PCM8 / ADPCM`
- `START PCM16 / PCM8 / ADPCM`
- `STOP`
- `SHIFT <10..20>`
- `INFO`

状态帧最后的 capture state 为 0 stopped、1 streaming、2 armed。旧固件只报告
0/1；上位机通过 3 秒 ARM ACK 超时识别旧固件或 BLE 断连。

### 与主 V0 协议合并时

不要删除 V0 ECG/IMU framing，也不要把音频强行塞进现有 68-byte V0 最大包。
优先复用 `MIC1` framing，并在唯一 BLE 连接的接收层增加双协议 demux，或者在明确
更新 C/Python 协议、golden vectors 和文档后再设计统一 envelope。

不能同时让 `BlePacketReader` 和 `MicBleClientThread` 分别连接同一设备。最终 PC
应用应只有一个 BLE 连接 owner：保留主 `BlePacketReader` 的绑定、原始记录、Health
source coordinator 和 V0 dispatch，再复用 `MicFrameParser` 和控制命令队列。

固件 TX 调度必须继续保证 ECG 原始流优先。ADPCM 音频可用独立低优先级队列，
但 STOP、ARM ACK、状态和错误不能被音频饿死。

## 7. Windows 麦克风上位机

入口和组件：

| 文件 | 复用职责 |
|---|---|
| `pc_app/src/smart_neckband/mic_capture_gui.py` | ASR/诊断页、状态机、校准、WAV、线程信号 |
| `pc_app/src/smart_neckband/mic_capture_ble.py` | CollarMic 扫描、单 BLE client、命令队列 |
| `pc_app/src/smart_neckband/mic_capture_protocol.py` | MIC1 parser、ADPCM、WAV recorder |
| `pc_app/src/smart_neckband/volc_asr_client.py` | 火山 V3 协议、WebSocket worker、队列/final |
| `pc_app/src/smart_neckband/audio_threshold_vad.py` | 当前 PC VAD 与校准算法 |
| `pc_app/src/smart_neckband/mic_capture_debug.py` | 结构化、脱敏 JSONL 日志 |
| `pc_app/pyproject.toml` | `smart-neckband-mic` entry point 与 GUI 依赖 |
| `tools/project.ps1` | `pc-mic` 启动入口和模块检查 |

独立 GUI 有两个主标签：

- `ASR`：火山配置、partial/final、VAD 参数和环境/说话标定；
- `诊断`：波形、RMS/Peak、BLE 帧/样本/gap/CRC、WAV 路径和手动波形测试。

推荐启动方式：

```powershell
.\tools\project.ps1 pc-setup
.\tools\project.ps1 pc-mic
```

`pc-mic` 会把工作目录切到 `pc_app`。这很重要，因为 ASR/VAD 配置和日志使用
`Path.cwd()/data`。直接从其他目录运行 exe 会把本地文件写到意外位置。

如果 `smart-neckband-mic.exe` 报 `ModuleNotFoundError`，先检查实际工作区是否在
`test/inmp441-ble-capture` 或已经合入这些文件，并重新运行 `pc-setup`。不要通过
复制单个 `.py` 到 site-packages 掩盖错误分支。

### Windows / Qt / BLE 约束

Bleak 扫描不得在 Qt GUI/STA 线程直接执行 `asyncio.run()`。当前实现用独立 Python
线程扫描，再通过 Qt signal 返回。这个修复来自与主 ECG GUI 相同的 WinRT 问题，
大合并时必须保留。

最终整机 GUI 应复用当前 worker/event 逻辑，但不应同时启动第二个 BLE 连接。
建议把麦克风 ASR/VAD 控件移入主 GUI 的独立标签或 controller，而不是复制 BLE
连接与 Webhook 实现。

## 8. 火山引擎流式 ASR

实现和测试：

- `pc_app/src/smart_neckband/volc_asr_client.py`
- `pc_app/tests/test_volc_asr_protocol.py`
- fixture：`pc_app/tests/fixtures/volc_asr_v3.json`

当前默认和协议事实：

| 项目 | 当前值 |
|---|---|
| Endpoint | `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel` |
| 音频 | PCM16 little-endian、16 kHz、mono |
| WebSocket 音频块 | 100 ms |
| ASR 音频队列 | 512 |
| receive polling 上限 | 20 ms |
| final 等待 | 5 s |
| 服务端 `end_window_size` | 800 ms |
| `force_to_speech_time` | 1000 ms |
| 鉴权 | API Key 或 legacy App Key + Access Key |

`3b92ad0` 修复了一个关键性能问题：发送循环不能被 `ws.recv()` 超时节流。当前
worker 会先快速 drain 已缓存音频并连续发出多个 audio frame，再短轮询服务端；
VAD 结束后使用更大的 drain budget，尽快发送尾部音频和 final 标记。

ASR 队列满时丢弃最旧音频并记录日志，不直接让整个会话失败。停止时会优先为
final marker 腾出空间。

ASR final 由 PC 产生。不要把 `volc_asr_client.py` 移到 ESP，也不要让 ESP 保存
火山密钥。

## 9. 当前 VAD：以环境音结束

实现和测试：

- `pc_app/src/smart_neckband/audio_threshold_vad.py`
- `pc_app/tests/test_audio_threshold_vad.py`

`912a416` 是当前权威行为：

- 采样率固定 16000 Hz，旧 JSON 中的 15000 会自动迁移；
- 不再暴露或使用“结束阈值比例”；
- 起始阈值依据静默 RMS、说话 RMS、RMS 倍数和最小增量计算；
- 结束阈值是“静默环境 RMS + 最小 RMS 增量”；
- 已开始说话后，连续回落到环境音范围 1000 ms 才触发 speech end；
- 没有最大录音硬截断；
- 默认最小说话时长 300 ms；
- 队列满时丢弃最旧帧并继续；
- 静默标定使用中位数与 80% 高位窗口的稳健结果；
- FunASR、PyTorch 和 torchaudio 均不需要。

用户在最新实时日志中发现 500 ms 会在句中停顿时截断；该轮日志证明触发原因是
VAD，而不是 BLE、ASR、队列或最长录音。随后配置改为固定 16 kHz、环境音结束、
1000 ms。此最新算法已通过自动化测试，但在本 handoff 截止时尚未再次完成用户
真人长句硬件验收。

## 10. 本地配置、日志和音频隐私

以下路径全部被 `.gitignore` 覆盖，不得提交：

| 路径 | 内容 |
|---|---|
| `pc_app/data/volc_asr_settings.json` | 火山 endpoint、Resource ID 和凭据 |
| `pc_app/data/audio_threshold_vad_settings.json` | 环境/说话 RMS 和 VAD 参数 |
| `pc_app/data/mic_capture_debug.log` | BLE、Wake、ASR、VAD JSONL 调试日志 |
| `pc_app/captures/` 或 `captures/` | WAV、特征图和测试录音 |
| `experiments/.../firmware/build/` | 构建产物和模型分区镜像 |

ASR/VAD 配置只在用户点击“保存 ASR/VAD 配置”或“保存采样”后写入，不自动保存。
日志会对字段名含 key/token/secret/password/authorization 的值脱敏。

实验 GUI 默认可以写 WAV。这与早期生产 Voice 文档“音频不落盘”的隐私目标不同。
大合并必须明确产品策略：建议正常唤醒默认不保存 WAV，只在用户打开诊断录音时
落盘；无论选择什么，都不得把录音提交到 Git。

## 11. Agent Webhook：必须复用，当前尚未接线

现有可靠实现：

| 文件 | 职责 |
|---|---|
| `pc_app/src/smart_neckband/webhook_client.py` | durable submit、HTTP 分类、重试 |
| `pc_app/src/smart_neckband/webhook_store.py` | SQLite instruction/reply 状态与去重 |
| `pc_app/src/smart_neckband/webhook_receiver.py` | `agent.reply.completed` callback server |
| `pc_app/src/smart_neckband/webhook_models.py` | settings、records、contract validation |
| `pc_app/src/smart_neckband/webhook_ui.py` | 主 GUI Webhook tab 和 dispatcher ownership |
| `docs/pc-agent-webhook.md` | Gateway 操作与安全说明 |

当前 `mic_capture_gui.py` 不导入 Webhook 模块，ASR final 只显示在文本框中。

大合并应在 `VolcAsrEvent(kind="final")` 到达后调用现有
`WebhookDispatcher.enqueue_text()`，而不是新写 HTTP 请求。必须先写 SQLite，再发
Gateway；重试使用相同 instruction ID 和原文。

稳定 instruction ID 仍需明确。不要只用 `wake_count`，因为设备重启后会重复。
可复用旧 `VoiceTranscript.instruction_id` 的稳定 ID 思路，或组合设备身份、
`detected_sample_index` 和会话标识。ID 规则确定后应写测试覆盖重复 final、重启和
同 ID 冲突。

Agent reply 应继续进入现有 reply receiver/SQLite/UI，不要另建麦克风专用 callback。

## 12. 与生产传感器固件合并

独立实验固件故意不包含 ECG、IMU、OLED、Wi-Fi、Agent 或生产 V0 任务。
大合并不能直接用它替换 `firmware/main`。

建议迁移顺序：

1. 以生产 C3 固件的板型配置、BLE 安全和传感器任务为宿主。
2. 从实验固件提取并复用 I2S、PCM shift、ADPCM、WakeNet、ARM/STOP 和 MIC1 编码。
3. 保留主固件 GPTimer ECG 采样、原始数据完整性和高优先级 TX。
4. 给语音音频独立有界低优先级队列，记录高水位、丢帧、I2S 和 TX 错误。
5. 在同一 BLE connection owner 中接入麦克风控制和音频 parser。
6. 把 ASR/VAD controller 接入主 PC GUI；不要启动独立第二连接。
7. ASR final 进入现有 Webhook dispatcher。
8. Agent reply 继续使用现有 callback 和 SQLite。

必须复用的生产边界：

- `firmware/components/neckband_interfaces/include/board_config.h` 及板型头文件；
- `firmware/main/ble_transport.c` 的加密 BLE 与生产 TX 行为；
- `firmware/main/packet_task.c` 的传感器包优先级；
- `pc_app/src/smart_neckband/ble_io.py` 的唯一连接和控制写队列；
- Health MCP 分支的 staged parser、source coordinator、raw-before-parse 和 receipt
  provenance（如果最终集成分支包含 Health MCP）。

## 13. 与 Health MCP 分支的已知合并关系

当前 peer 分支：

- `origin/feat/health-mcp-v0` at `e8656ac`；
- 麦克风分支 at `912a416`；
- 共同祖先是 `abe97de`。

在本 handoff 修改之前，`git merge-tree` 显示两个内容冲突：

1. `pc_app/pyproject.toml`
2. `tools/project.ps1`

本次根 `HANDOFF.md` 重写后，最终大合并还应预期一个文档冲突；不要简单选择 ours
或 theirs。应把各子系统 handoff 归档到 `docs/handoffs/`，再生成最终总 handoff。

冲突解决规则：

### `pc_app/pyproject.toml`

- 保留 `smart-neckband-mic` entry point；
- 保留 GUI 的 `bleak`、`numpy`、`pyqtgraph`、`PySide6`、`websocket-client`；
- 保留 Health MCP 的 `mcp==1.28.0` health extra；
- 保留主 GUI、serial、BLE、upload extras；
- 不加入 FunASR、torch 或 torchaudio。

### `tools/project.ps1`

- 保留麦克风的 `pc-mic`；
- 保留主固件 build/size/flash 和 Voice provisioning actions；
- 保留 Health MCP 的 `pc-health-mcp`、status、admin、soak 等命令；
- 保留各命令的正确 venv 模块检查和工作目录；
- 不用一个功能的 action 列表覆盖另一个功能。

Health MCP 的详细复用要求在 `origin/feat/health-mcp-v0:HANDOFF.md`。大合并 Agent
必须同时阅读该文档，尤其不能移除 staged packet commit、SourceInstanceCoordinator、
receipt-aware buffers、SQLite health store 或 MCP 审计。

## 14. 已完成验证

### PC 软件

当前提交 `912a416`：

| 验证 | 结果 |
|---|---|
| `pc_app/tests/test_audio_threshold_vad.py` | 9 passed |
| `tools/project.ps1 pc-test` | 93 passed |
| `git diff --check` | passed |
| 麦克风 GUI 模块导入 | passed |
| `smart-neckband-mic` entry point | 在正确分支和 venv 下可启动 |

测试入口：

| 测试文件 | 覆盖 |
|---|---|
| `pc_app/tests/test_mic_capture_protocol.py` | MIC1、切分、CRC、ADPCM、状态、WAKE、WAV |
| `pc_app/tests/test_volc_asr_protocol.py` | 火山请求/响应、final、快速 drain、队列 |
| `pc_app/tests/test_audio_threshold_vad.py` | 阈值、环境结束、旧配置迁移、队列 |
| `pc_app/tests/test_ble_io.py` | 生产 BLE 与可靠 voice text/ACK |
| `pc_app/tests/test_voice.py` | UTF-8 文本重组、重复和冲突 |
| `pc_app/tests/test_webhook.py` | durable Agent Webhook、reply callback 和去重 |

### 固件与硬件

已验证事实：

- ESP-IDF v6.0.2 和 ESP-SR 2.4.6 已成功构建实验固件；
- 官方模型为 `wn9s_hiesp`，model image 约 125,944 bytes；
- 最新烧录使用 app 624,768 bytes，model 分区写入 `0x190000`；
- 2026-07-25 COM21 烧录 bootloader、partition、app、srmodels，四项哈希均通过；
- 硬复位后扫描到 `CollarMic-2E4A`；
- 实际说出 Hi ESP 能产生 WAKE 事件；
- 实际中文语音已得到火山 partial 和 final；
- 早期 3.025 秒 ADPCM smoke run：121 帧、48,400 样本、16 kHz、sequence gap 0、
  CRC 0、I2S/TX/clipping error 0，连接间隔 15 ms；
- STOP 后可再次 ARM，无需断电。

不要把“最新 1000 ms 环境音 VAD”标为真人硬件通过；它在 handoff 截止时只有软件
测试，下一轮应专门用含 0.5–0.9 秒句中停顿的长句验收。

## 15. 已踩坑与不可回归行为

| 问题 | 已确认原因 | 必须保留的修复 |
|---|---|---|
| PCM8/PCM16 丢帧 | Windows BLE 吞吐和控制写被音频饿死 | 默认 ADPCM，短块、队列、连接参数 |
| I2S 周期性超时 | `i2s_channel_read()` 参数是毫秒，误传 tick 转换后只有 20 ms | 直接传 200 ms，不用 `pdMS_TO_TICKS(200)` |
| Qt 扫描卡死/无回调 | Bleak/WinRT 在 Qt STA 线程直接跑 asyncio | 扫描放独立 Python 线程 |
| 第一次 STOP 后不能再测 | STOP 未即时清 armed/streaming 和旧 TX | BLE RX 立即 STOP + 清队列 + 状态 ACK |
| ARM 未确认后又弹 STOP Unreachable | 超时处理向失联设备再次发 STOP | ARM 超时只结束本地录制并提示，不补 STOP |
| ASR final 十几秒后才出现 | 单线程发送被 `recv()` timeout 节流 | 快速 drain 多帧，20 ms receive polling |
| ASR/VAD 队列满即失败 | 有界队列没有降级策略 | 丢弃最旧音频并记录计数 |
| FunASR VAD 报依赖/队列错误 | PyTorch/FunASR 过重且运行链不稳定 | 已删除，使用音频阈值 VAD |
| 句中停顿 500 ms 被截断 | 结束阈值和时间过于激进，且旧配置误设 15 kHz | 固定 16 kHz，回落环境音 1000 ms |
| exe 找不到 `mic_capture_gui` | Windows 实际工作区停在不含麦克风模块的分支 | 切到正确分支/完成合并，再用 `pc-setup`，不复制模块 |

## 16. 合并后的最小验收清单

### 软件

1. 运行完整 `pc-test`，保留 Health/MCP、Webhook、主 V0 和麦克风测试。
2. 运行经典 ESP32、普通 C3 和最终语音 C3 的 build/size。
3. 检查 `pyproject.toml` 同时包含 mic 和 health 依赖/入口。
4. 检查 `tools/project.ps1` 同时包含 mic、health、主 GUI 和固件命令。
5. 检查主 GUI 只有一个 BLE connection owner。
6. 检查 ASR final 只产生一次 durable Webhook instruction。
7. 检查重复 final/重连/进程重启不会重复 Agent 指令。
8. 运行 `git diff --check`。

### 无人体电极硬件台架

1. Hi ESP 20 次唤醒命中率和误唤醒统计。
2. 10 条含 0.5–0.9 秒句中停顿的长句，不得被 VAD 截断。
3. 真实静默后约 1 秒完成 final，记录 wake-to-partial、wake-to-final 延迟。
4. ECG 500 Hz + IMU 50 Hz + ADPCM 音频并发至少 30 分钟。
5. CRC、sequence gap、ECG missed、transport overflow、audio drop、I2S error、WDT、
   OOM 和最小空闲 heap 全部记录。
6. STOP、再次 ARM、BLE 断开重连和设备重启恢复。
7. ASR final -> Agent Gateway 202 -> callback final reply 全闭环。

## 17. 安全和秘密边界

- USB 烧录和调试只允许在没有人体电极连接时进行。
- 人体 ECG 必须独立电池供电并使用无线传输；电极连接人体时不得接桌面 USB、
  墙充、正在充电的移动电源或接地仪器。
- Flash、erase、eFuse、partition change、串口 monitor 和人体采集都需要用户明确授权。
- INMP441 VDD 只接 3.3 V。
- 火山密钥只保存在 PC ignored JSON 或安全环境变量，不进入 Git、不进入 ESP Flash、
  不进入日志和 handoff。
- WAV 和调试日志可能包含私人语音，只能保存在 ignored 本地目录。
- 该系统不是医疗设备、紧急服务或机器人动作授权系统。

## 18. 当前未完成事项

1. 把实验麦克风运行时合入生产传感器固件，而不是替换生产固件。
2. 在一个加密 BLE 连接中同时承载 V0 传感器流和 MIC1 音频/控制。
3. 把 ASR/VAD UI 合入主 GUI，避免第二个 BLE 连接。
4. 把 ASR final 接入现有 durable Agent Webhook dispatcher。
5. 确定稳定的麦克风 instruction ID 和重复 final 去重规则。
6. 决定正常唤醒是否默认保存 WAV；建议默认不保存，仅诊断显式开启。
7. 完成最新环境音 1000 ms VAD 的真人长句验收。
8. 完成 ECG/IMU/麦克风并发压力与内存验证。
9. 如果未来重新要求“主人主人”，单独取得并验证官方模型；不要在本次合并中
   用本地训练或相似词替换 Hi ESP。

## 19. Source of truth 优先级

发生冲突或文档不一致时，按以下顺序判断：

1. 当前分支提交 `912a416` 的源码和测试；
2. 本 `HANDOFF.md`；
3. `docs/plans/2026-07-24-inmp441-ble-capture.md` 的硬件实验记录；
4. `docs/voice-wake-asr.md` 的历史背景；
5. 更早的 Voice ExecPlan。

构建产物、local JSON、WAV 和 debug log 不是版本化 source of truth。

## 20. 最终 handoff 规则

把这个分支视为已经完成实板验证的“麦克风实验实现”和已经完成软件验证的
“PC ASR/VAD 实现”。大合并应适配调用点、连接 owner、队列优先级和 GUI 组合，
而不是重写协议、ADPCM、火山客户端、VAD、Webhook 或日志。

如果合并需要改变 wire format、Agent 去重语义、音频隐私策略或传感器优先级，必须
在同一变更中更新协议文档、C/Python 测试、golden vectors、操作文档和本 handoff。
