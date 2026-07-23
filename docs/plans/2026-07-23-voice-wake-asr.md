# ESP32-C3 麦克风、离线唤醒与火山 ASR 链路

## Goal

在现有 ESP32-C3 传感器与 BLE V0 数据流上增加一条语音链路：

```text
INMP441 -> ESP-SR/WakeNet9s “主人主人” -> 火山引擎流式 ASR
         -> BLE V0 文本包 -> PC 持久化 -> Agent Webhook
```

ECG 原始采样与传输优先级不变。只有非空的 ASR 最终文本可以被自动转发到 Agent。

## Current state

- 工作分支从包含 PC Agent Webhook 的提交 `c08e696` 创建。
- 当前语音目标板为 ESP32-C3 SuperMini；普通 ESP32-C3 和经典 ESP32 构建仍需保持可用。
- C3 通过 BLE UART 发送 V0 二进制字节流，ECG、IMU、状态包由同一个增量解析器处理。
- PC Webhook 已有 SQLite 持久化、稳定 instruction ID、重试和回调接收能力。
- 固件尚无麦克风、Wi-Fi、ESP-SR、WebSocket 或 ASR 代码。
- 当前未跟踪的健康 MCP 计划和规范属于其他工作，不纳入本分支。

## Scope

包含：

1. 新增 V0 语音文本分片、语音状态和文本 ACK 包。
2. PC 端分片重组、UTF-8 校验、SQLite 先持久化后 ACK、自动 Webhook 转发。
3. ESP32-C3 Voice 构建配置、INMP441 I2S 输入、语音状态机和火山 ASR 客户端边界。
4. USB 写入专用 NVS 分区的配置工具设计与实现。
5. 协议文档、黄金向量、单元测试和构建验证。

不包含：

- TTS、连续对话、Agent 回复回传设备、SoftAP 配网、OTA。
- 将麦克风音频通过 BLE 发送或保存到日志。
- 自动烧录、串口监视、人体连接采集或医疗结论。

## Design decisions

### Gate 0：精确唤醒词模型

- 最终实现必须使用适配 ESP32-C3 的官方 WakeNet9s 自定义模型“主人主人”。
- 模型需要明确名称、SHA-256、许可证和不超过 1 MiB 的打包尺寸。
- 不以“你好小智”或其他内置词替代最终交付。
- 默认不提交可能受限的模型二进制；仓库提交校验清单，并由需要单独授权的
  `voice-model-provision` 操作校验本地模型后只写入 `model` 分区。

### 硬件与音频

- C3 暂定 INMP441：BCLK GPIO4、WS GPIO5、SD GPIO20、L/R 接 GND、3.3 V 供电。
- GPIO5 与当前板文件的保留规则冲突；在确认实际 SuperMini 引脚可用性前，不写死或烧录该映射。
- I2S 标准模式，16 kHz 单声道，32-bit slot，集中转换为 PCM16，默认右移 14 bit。
- 语音任务不得阻塞 ECG 采样任务，也不得改写或过滤原始 ECG 数据。

### 构建与分区

- `-Voice` 仅允许与 `-Target esp32c3` 组合。
- 固定 ESP-SR 2.4.6 与 `esp_websocket_client` 1.7.0。
- Voice 分区：2 MiB factory、1 MiB model、24 KiB voicecfg NVS、余下空间为 storage。
- 普通 C3 和经典 ESP32 继续使用现有分区，不依赖本地唤醒模型。

### 运行时

- 状态：`DISABLED`、`IDLE_WAKE`、`ASR_CONNECTING`、`STREAMING`、`WAIT_FINAL`、`ERROR_COOLDOWN`。
- Wi-Fi 常驻关联；WSS 每次唤醒后建立。
- 唤醒后保留最多 1 秒/32 KiB PCM16 预卷；溢出时中止本轮，不发送缺头文本。
- 火山固定入口 `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel`。
- 上行 PCM16/16 kHz/mono，每帧 100 ms。
- 服务端 VAD `end_window_size=800`；5 秒无语音、15 秒总时长、5 秒最终结果等待超时。
- partial 仅供状态显示；只有非空 final 文本进入 BLE。
- 任一语音错误只终止当轮并恢复唤醒，传感器链路继续运行。

### USB/NVS 配置

- `voicecfg` 保存版本、SSID/密码、鉴权模式、AppID、API key、Access Token、Resource ID。
- `.\tools\project.ps1 voice-provision -Target esp32c3 -Voice` 安全提示输入、生成临时 NVS 镜像，仅写 `voicecfg` 分区后删除临时文件。
- 该动作属于烧录，只有用户再次明确授权且人体电极未连接时才执行。
- 未启用 Flash Encryption 时普通 NVS 不具备硬件级秘密保护，文档必须明确。

### V0 协议

- 保持现有 18-byte header、全局 sequence、CRC16-CCITT-FALSE 和 68-byte 最大包长。
- `VOICE_TEXT_CHUNK = 4`，48-byte payload：`utterance_id:u64`、`chunk_index:u8`、`chunk_count:u8`、`text_length:u8`、`flags:u8`、`text[36]`。
- `VOICE_STATUS = 5`，固定状态、标志、最近错误和计数。
- `VOICE_TEXT_ACK = 6`，8-byte `utterance_id`，方向为 PC 到设备。
- 最终文本最大 512 UTF-8 bytes；PC 按 byte 分片重组并严格校验 UTF-8。
- 设备最多保留 4 条待 ACK 文本；BLE 已连接时每 2 秒重发，重启后不持久化。
- Voice TX 为低优先级，不得挤出 ECG 包。
- PC durable insert 成功后才发送 ACK；稳定 instruction ID 为 `voice-<utterance_id 的 16 位小写十六进制>`。
- 同 ID 同文本复用持久化记录并再次 ACK；同 ID 冲突或缺片不 ACK、不转发。
- Gateway 请求体继续为 `{instruction_id, text}`。

## Work breakdown

1. 创建功能分支和本 ExecPlan，保存现有未跟踪健康规范文件。
2. 扩展 C/Python 协议、黄金向量和双向 BLE 写入接口。
3. 实现 PC 文本重组、SQLite 持久化、ACK 和自动 Webhook 调度。
4. 增加 GUI 语音状态、最终文本、错误和待处理数量。
5. 增加 C3 Voice 构建开关、分区、模型清单和 NVS provisioning。
6. 实现 INMP441、WakeNet、ASR 会话和低优先级可靠文本队列。
7. 更新协议、硬件、隐私、安全和操作文档。
8. 完成软件验证；硬件验证等待单独授权与官方模型。

## Validation

软件：

```powershell
.\tools\project.ps1 build -Target esp32c3 -Voice
.\tools\project.ps1 size -Target esp32c3 -Voice
.\tools\project.ps1 build -Target esp32c3
.\tools\project.ps1 size -Target esp32c3
.\tools\project.ps1 build -Target esp32
.\tools\project.ps1 size -Target esp32
.\tools\project.ps1 pc-test
git diff --check
```

测试覆盖：

- 中文 UTF-8 多字节边界和任意 BLE notification 切分。
- 乱序、重复、冲突、缺片、截断、ACK 丢失和重试。
- SQLite 先于 ACK，重复文本只产生一条 Agent instruction。
- WebSocket 分片、partial/final、错误、超时和畸形响应 fixture。
- C 与 Python 新包黄金向量一致。

硬件（需另行授权，且不得连接人体电极）：

- 检查 INMP441 16 kHz、幅度、削顶和 DMA overflow。
- 安静环境 0.5 m 处“主人主人”20 次至少唤醒 18 次。
- 普通说话/噪声 30 分钟无误唤醒。
- 10 条普通话命令各产生且只产生一条 final 和一条持久化 Webhook instruction。
- BLE + Wi-Fi + WakeNet 连续 30 分钟、至少 10 次 ASR；CRC、sequence gap、missed notification、ECG ring overflow、transport overflow、WDT/OOM 均为零。
- 记录唤醒至 final 延迟和最小空闲 heap。

## Risks and rollback

- C3 无 PSRAM，模型、TLS、BLE 和 32 KiB 预卷可能同时造成内存压力；以构建 size 和硬件 heap 记录为准，不能通过降低 ECG 完整性换内存。
- GPIO5 当前被板文件标记为保留；接线确认前保持 Voice 构建禁用。
- 自定义模型不可获得时，停在 Gate 0，不将内置词标为完成。
- 火山鉴权或协议升级通过独立 ASR 适配层隔离，失败不影响传感器任务。
- 语音分支可整体撤销；普通目标不启用 Voice，因此保留当前行为。

## Progress

- [x] 从 `c08e696` 创建 `feat/voice-wake-asr`。
- [x] 创建 ExecPlan。
- [x] 完成 V0 语音协议和黄金向量。
- [x] 完成 PC 重组、持久化、ACK 和自动转发。
- [x] 完成 C3 Voice 构建与运行时。
- [x] 完成软件验证。
- [ ] 获得并验证“主人主人”官方 WakeNet9s 模型。
- [ ] 完成授权后的硬件验证。

## Discoveries

- 2026-07-23：开始时工作树位于 `docs/health-mcp-spec`，含未跟踪的健康 MCP 文件；已保留并明确排除。
- 2026-07-23：当前 C3 板文件把 GPIO5 作为 ADC2/保留脚规避，计划中的 INMP441 WS=GPIO5 必须先经实板证据确认。
- 2026-07-23：ESP-SR 的 C3 内置 WakeNet9s 列表不含“主人主人”，精确词必须走官方自定义模型 Gate 0。
- 2026-07-23：Voice 依赖固定为 ESP-SR 2.4.6、`esp_websocket_client`
  1.7.0 和 cJSON 1.7.19~2；普通构建不链接这些依赖。
- 2026-07-23：ESP-IDF 的早期依赖扫描要求始终发现 `voice_deps` manifest；
  通过独立组件和 `SMART_NECKBAND_VOICE` 条件链接，普通固件仍保持原体积。
- 2026-07-23：新增语音包后 V0 最大包仍为 68 bytes；C3 普通构建产物
  `0x84e90` bytes，app 分区剩余 74%，DRAM 使用 121261 bytes（37.74%）。
- 2026-07-23：经典 ESP32 普通构建产物 `0xa1000` bytes，app 分区剩余
  69%，IRAM 使用 68.9%，DRAM 使用 59.86%。
- 2026-07-23：C3 Voice 构建产物 `0x164ce0` bytes，2 MiB app 分区剩余
  30%，DRAM 使用 185409 bytes（57.71%）。构建报告明确为
  `No speech models loaded`，符合官方精确模型尚未供应的 Gate 0 状态。
- 2026-07-23：火山 V3 流式协议允许不压缩，请求和服务端响应均使用
  no-compression，避免在无 PSRAM 的 C3 上引入 gzip 峰值内存。
- 2026-07-23：PC 测试 70 项全部通过；沙箱内 pytest 临时目录和
  `idf.py` pipe 均出现已知 `WinError 5`，在获批的沙箱外相同命令验证通过。

## Result

软件实现和软件验证已完成。Voice、普通 C3、经典 ESP32 构建与 size 均通过，
PC 70 项测试通过，`git diff --check` 通过。当前交付会在缺少精确
“主人主人”官方模型时安全停在 Gate 0，不会用其他内置词替代。

尚未完成的只有需要外部资源或单独授权的项目：获取并校验官方自定义 WakeNet9s
模型、写入模型与 voicecfg 分区、INMP441 实板接线确认、真实 Wi-Fi/火山账户
端到端 ASR，以及无人体电极连接条件下的稳定性与延迟测试。本次未烧录、未打开
串口监视器、未执行人体连接采集。
