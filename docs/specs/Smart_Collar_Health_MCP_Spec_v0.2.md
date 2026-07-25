# AI 智能项圈 Health MCP 接口规范

> 契约版本：`0.2.0`
> 文档状态：可进入实现；尚未表示代码已经实现
> 日期：2026-07-23
> MCP 协议基线：`2025-11-25`
> 机器可读契约：[`health-mcp-v0.2.contract.json`](health-mcp-v0.2.contract.json)
> 适用仓库：`human-chocker-and-robot-dog/smart-neckband`

## 0. 阅读方式与规范性约定

本文档替代未冻结的 v0.1。v0.1 不作为兼容基线，不要求实现方兼容其工具、字段、事件或 HTTP 行为。

本文使用以下规范词：

- **必须**：实现和测试都不得省略；
- **不得**：明确禁止；
- **应该**：无充分理由不得偏离，偏离时必须在实现说明中记录；
- **可以**：可选能力，不得成为 P0 主链依赖。

字段级约束、正则、上下界、`required`、`additionalProperties`、MCP 工具 `inputSchema` / `outputSchema`、Webhook 请求和 ACK Schema，以机器可读契约为准。本文与契约冲突时：

1. 安全和原始数据规则以仓库根目录 `AGENTS.md` 为最高约束；
2. 字段结构以机器可读契约为准；
3. 行为语义以本文为准；
4. 仍有冲突时不得自行猜测，必须先修正文档和契约。

本文中的“健康”表示设备和信号的工程状态，不表示医疗级有效性，不构成诊断、治疗、急救或风险预测。

## 1. 一页结论

### 1.1 当前仓库已经具备的基础

| 能力 | 当前事实 | 实现锚点 |
|---|---|---|
| 固件实时协议 | 二进制 V0；当前 ESP32-C3 使用 USB UART 台架链路与 BLE，历史 SPP 名称仅作合同兼容 | `docs/protocol/v0.md` |
| ECG | 500 Hz，原始 ADC counts，不在固件过滤 | `firmware/main/protocol_v0.*`、`firmware/main/sensors.*` |
| IMU | 50 Hz，原始六轴数据 | `firmware/main/sensors.*` |
| 设备状态 | lead-off、clipping、采样/队列/传输/I2C 计数 | `DeviceStatusPayload` |
| 语音文本传输 | 已推送 `feat/voice-wake-asr` commit `17fc0df`：device→PC `VOICE_TEXT_CHUNK` / `VOICE_STATUS`，PC→device `VOICE_TEXT_ACK` | `docs/protocol/v0.md`、`pc_app/src/smart_neckband/protocol.py` |
| PC 解析 | CRC、长度、版本、序列缺口、ECG/IMU/status/voice 解码与环形缓冲 | `pc_app/src/smart_neckband/protocol.py`、`pc_app/src/smart_neckband/serial_io.py`、`ble_io.py` |
| PC ECG 分析 | NeuroKit2，10 秒窗口；cleaned ECG、R peaks、HR、RR、SQI | `pc_app/src/smart_neckband/analysis.py` |
| 本地连接状态 | `RECEIVING`、`STALE`、`ERROR` 等 | `pc_app/src/smart_neckband/status.py` |
| Live Web | 可视化用的 cleaned ECG、HR、SQI 和状态 | `pc_app/src/smart_neckband/live_uploader.py`、`lib/protocol.ts` |

### 1.2 当前仓库明确没有的能力

- 没有 Health MCP Server；
- 没有健康事件持久化状态机；
- 没有 HRV；
- 没有 `still / light / active` 运动分类器；
- 没有姿态驱动的健康判断；
- 没有 `high_hr_low_motion` 的已验证算法；
- 没有机器人定位、闭环接近、避障或 DimOS 客户端；
- 没有可保证机器人动作安全的确定性策略门；
- 没有医疗验证。

因此，本文把 P0 缩到本仓库能直接实现和验证的范围：**PC 侧只读 Health MCP + 独立、持久化、可验签的健康状态 Webhook**。任何机器人动作均不属于本契约。

### 1.3 P0 最终边界

```text
ESP32-C3
  │  固件二进制 V0：raw ECG + raw IMU + device status
  ▼
PC PacketParser / PcDataStores
  ├─ raw binary recorder（现有、可选、仍是原始证据）
  ├─ NeuroKit2 analysis worker（现有）
  └─ HealthStateBuilder（新增）
       ├─ HealthStateStore（新增，最新状态）
       ├─ HealthEventStore（新增，事件与投递状态）
       ├─ Health MCP Server（新增，只读）
       └─ Health Webhook Dispatcher（新增，只发唤醒通知）

外部 Agent / 规则层 / DimOS / 机器人
  └─ 仅作为契约消费者；不属于本仓库的已实现能力
```

P0 的 Health MCP 和 Webhook 均由 Windows PC 侧承载，不放进 ESP32 固件，不修改固件采样、二进制协议、传输接口或 OLED。

语音识别和普通 Agent webhook 不属于 Health MCP 功能，但它们共享 V0 parser/reader，因此本文必须约束 packet ordering、reset buffering 和 queue 隔离。Health 实现分支应在 `17fc0df` 已合入的集成基线上开发；如果该 feature 尚未进入 `main`，不得删除语音包兼容规则来规避集成。

## 2. 数据所有权与不可破坏规则

### 2.1 数据层次

| 层 | 数据 | 权威性 |
|---|---|---|
| L0 | 固件 V0 二进制包、原始 ADC counts、原始 IMU、设备 flags/counters | 原始采集权威源 |
| L1 | PC `PacketParser` 解析结果与 parser stats | 传输完整性权威源 |
| L2 | PC NeuroKit2 输出：cleaned ECG、R peaks、HR、RR、SQI | 派生分析；非诊断 |
| L3 | `WearerState`、`WearerEvent` | 面向 Agent 的工程摘要 |
| L4 | Webhook 唤醒通知 | 非权威，只提示消费者查询 L3 |

任何 L2、L3、L4 都不得覆盖、修改、插值、丢弃或替换 L0 原始 ECG。Health MCP 不提供连续原始 ECG，Webhook 不携带 ECG 波形或生理数值。

### 2.2 时间与重启边界

固件 V0 当前只有设备单调 `timestamp_us` 和序列号，没有 `boot_id`。P0 不修改固件协议，而是在每次 PC 采集 reader 启动时生成一个 UUID `source_instance_id`：

- `source_instance_id` 是 **PC 生成的采集实例 ID**，不得描述成设备 boot ID；
- 同一 `source_instance_id` 内，`device_timestamp_us` 和 sample index 才可用于排序；
- 新 reader、新端口连接、应用重启或按下述确定性算法确认设备时间回退时，必须生成新的 `source_instance_id`；
- 跨 `source_instance_id` 不得用 `device_timestamp_us` 比较先后；
- `state_revision` 和 `notification_sequence` 由 PC 的 SQLite 状态库持久化递增，用于跨进程排序。

同一 reader 内的设备重启检测使用一个 **全局 pending-reset window**：

1. 对所有从设备进入 PC、通过 CRC 和 `PacketParser` 解码的 V0 packet 维护一个全局接收顺序；当前入站类型为 `ECG_BATCH`、`IMU_BATCH`、`DEVICE_STATUS`、`VOICE_TEXT_CHUNK` 和 `VOICE_STATUS`，仅 PC→device 的 `VOICE_TEXT_ACK` 明确不属于入站范围；
2. 按 packet type 记录已接受的最大 `timestamp_us`，同时记录最后一个正常前进的设备全局 `packet_sequence`；当前五种入站类型都可成为 reset trigger/confirmation；
3. uint32 sequence 的 forward distance 定义为 `(new - last) & 0xffffffff`；`1..0x7fffffff` 是正常前进，包含合法 wrap；
4. 某个有效 CRC 入站 packet 同时满足“sequence 非正常前进”和“同 packet type 的 `timestamp_us` 比已接受最大值回退超过 `1_000_000 us`”时，它成为唯一 trigger candidate；当前 trigger-eligible 类型为 `ECG_BATCH`、`IMU_BATCH`、`DEVICE_STATUS`、`VOICE_TEXT_CHUNK` 和 `VOICE_STATUS`；
5. 从 trigger candidate 开始，暂停向旧实例发布 **所有入站 V0 packet type** 的 packet，按接收顺序缓存在一个全局 window；这包括 `VOICE_TEXT_CHUNK` 和 `VOICE_STATUS`，但不包括仅出站的 `VOICE_TEXT_ACK`；pending 期间不得再建立 per-type candidate；
6. window 上限为 2 秒 PC 单调时间、256 个 packet 或 64 KiB raw packet bytes，任一先到即失败；
7. 确认条件是 window 中下一条与 trigger 相同 packet type 的有效 packet：它相对 trigger sequence 正常前进、timestamp 不小于 trigger，并且仍比旧实例该 type 的最大 timestamp 低超过 `1_000_000 us`；
8. 确认后只旋转一次 `source_instance_id`：清空 ECG/IMU/status 的 per-source ring、分析窗口、trigger maxima 和 per-source loss baseline；保留独立 lifetime diagnostics 和已经 durable commit 的 voice text；再把 window 内全部有效入站 packet 按原接收顺序归入新实例，voice chunk 仍经过其 `utterance_id`/chunk 幂等逻辑；
9. 确认 transaction/重放完成前，不得对 window 中任何 packet 发布 state、event 或 webhook，也不得提前 ACK buffered voice text；
10. 确认失败、超时或超过容量时，不旋转实例：丢弃 trigger candidate；其余 buffered packet 按原顺序、以“candidate 从未出现”的旧实例 sequence/timestamp 基线重新验证，正常 packet 发布到旧实例，异常 packet 丢弃并增加 stale/duplicate diagnostic；
11. pending window 清空后才允许建立下一 candidate，保证一次确认只产生一次实例旋转。

当前 `VOICE_TEXT_CHUNK` 重传只复用 `utterance_id` 和文本；`17fc0df` 每次发送/重传都会取得新的 V0 `packet_sequence` 和当前 `timestamp_us`。因此 `RETRANSMIT` payload flag 与应用层文本去重不得改变 reset header 判定。协议增加新的 device→PC packet type 时，它默认进入全局 pending buffer 且 trigger-eligible；只有 wire 协议明确允许复用旧 header sequence/timestamp 时，才能在同一协议变更中提供检测完整性证明并显式排除。

必须覆盖恰好回退 `1_000_000 us`、回退 `1_000_001 us`、uint32 正常 wrap、单个旧包、两包确认、ECG trigger 期间 IMU/status/`VOICE_TEXT_CHUNK`/`VOICE_STATUS` 交错、同 utterance 的正常重传使用前进 header 且不误触发、真实 reboot 后两个 voice chunk 可 trigger/confirm、确认后 voice 幂等重放、失败后旧实例重放、容量上限和只旋转一次的边界测试。

### 2.3 ECG sample index 的 wrap 与公开语义

V0 wire 的 `first_sample_index` 是 `uint32`，会合法 wrap。本文和机器契约中的 `WearerState.ecg_sample_index`、`HeartState.analyzed_through_ecg_sample_index` 不是原始 uint32，而是 PC 在每个 `source_instance_id` 内扩展出的单调 **ECG sample ordinal**：

```text
last_raw       = 上一个 committed ECG sample 的 uint32 index
last_ordinal   = 上一个 committed ECG sample 的扩展 ordinal
forward        = (new_batch_first_raw - last_raw) & 0xffffffff

1 <= forward <= 0x7fffffff:
  new_batch_first_ordinal = last_ordinal + forward
forward == 0:
  duplicate
forward > 0x7fffffff:
  stale/backward packet
```

每个 batch 内第 `i` 个样本的 ordinal 为 `new_batch_first_ordinal + i`，对应 raw index 为 `(new_batch_first_raw + i) & 0xffffffff`。新 source 的第一个 ordinal 直接取其无符号 raw first index；之后可超过 `0xffffffff`，但必须保持在 JSON safe integer `0..9007199254740991`。跨 source 不比较 ordinal。

因此 provenance 的 `analyzed_through_ecg_sample_index <= WearerState.ecg_sample_index` 使用扩展 ordinal 比较，绝不直接比较 raw uint32。必须覆盖一个从 raw `0xfffffff0` 开始、20 个样本跨 wrap、下一 batch 从 raw `4` 开始的 ring/analysis/state/golden 测试；预期 expanded last 为 `4294967299`、下一 expanded first 为 `4294967300`。

扩展必须在 coordinator commit 后、写 ring/runtime 前统一执行，不能由 state builder 临时补算。现有 `SerialRuntimeStatus.last_ecg_sample_index` 必须替换为两个不混用的字段：

```text
last_ecg_sample_ordinal: int      # 扩展、单调；Health/analysis 使用
last_ecg_raw_sample_index: int    # uint32，仅协议诊断
```

ECG ring batch metadata 同时保存 `raw_first_sample_index` 和 `first_sample_ordinal` / `last_sample_ordinal`；analysis snapshot、`EcgAnalysisResult.analyzed_through_ecg_sample_index`、`WearerState.ecg_sample_index` 和 runtime `last_ecg_sample_ordinal` 必须来自同一个 committed ordinal extender。serial 与 BLE 不得继续各自执行 `payload.first_sample_index + len(samples) - 1` 作为 Health index。

wrap golden 的第二个 20-sample batch从 raw `4` 开始后：

```text
second expanded first = 4294967300
second expanded last  = 4294967319
runtime raw last      = 23
runtime ordinal last  = 4294967319
```

### 2.4 UTC 时间与新鲜度

所有外部时间使用 UTC RFC 3339、毫秒精度和 `Z` 后缀，例如：

```text
2026-07-23T02:10:00.123Z
```

新鲜度不得只用墙上时钟相减。必须用 PC 单调时钟记录最新证据的接收时刻：

```text
age_ms = floor((monotonic_now - evidence_received_monotonic) * 1000)
```

规则：

- `age_ms` 最小为 `0`；
- `WearerState.age_ms`、`WearerState.freshness`、`observed_at`、`device_timestamp_us` 和 `ecg_sample_index` 都锚定到最后一个已接受、至少包含一个样本的 `ECG_BATCH`，不得被 IMU、device status、voice 或其他非 ECG packet 刷新；
- `DeviceState.last_ecg_packet_age_ms` 使用上述 ECG 接收单调时间；从未收到 ECG 时为 `null`；
- `DeviceState.last_transport_packet_age_ms` 使用任何已接受的 device→PC V0 packet 的接收单调时间；从未收到入站 packet 时为 `null`；仅出站 `VOICE_TEXT_ACK` 不得刷新它；
- 应用重启后，在收到新包前不得恢复旧状态为 `live`；
- `generated_at` 是响应生成时间，不是采样时间；
- `observed_at` 是 PC 收到相关证据时记录的 UTC 时间；
- `device_timestamp_us` 是设备单调时间；
- 某个 HR/SQI 指标的 `age_ms` 必须基于产生该分析结果的最新 ECG 样本，而不是分析线程结束时间。

P0 新鲜度分级：

| 条件 | `freshness` | 说明 |
|---|---|---|
| ECG `age_ms <= 2000` 且 reader 正常 | `fresh` | 与本地 PC 的 2 秒 ECG stale 判断一致 |
| `2000 <` ECG `age_ms <= 10000` 且 reader 正常 | `stale` | 即使 voice/IMU/status 仍到达，也不得用于需要实时性的自动决策 |
| ECG `age_ms > 10000`、reader 关闭或 reader error | `offline` | 即使 transport 仍有非 ECG 流量，也不得使用旧生理值替代 |
| 从未收到有效 ECG | `unavailable` | 不创建 `WearerState`；`health.get_device_status` 仍可报告 transport |

调用者传入的 `max_age_ms` 只能收紧标准，不能把 `stale` 或 `offline` 放宽为 `fresh`。

`DeviceState.status` 与生理 `freshness` 分开计算：

| transport / ECG 条件 | `device.status` |
|---|---|
| reader error/关闭，或 transport packet age `>10000 ms` | `offline` |
| transport packet age `(2000,10000] ms` | `stale` |
| transport fresh，但 ECG age `>2000 ms`、采样停用、拥塞或设备错误计数在当前窗口增加 | `degraded` |
| transport fresh、ECG fresh，且无上述降级证据 | `ok` |

因此，“voice 仍在线但 ECG 已停止”必须表现为 transport 仍 connected/recent、`device.status=degraded`，同时 `WearerState.freshness=stale|offline` 并触发相应 input event；二者不得互相覆盖。

## 3. 实现落点

建议实现时新增以下 PC 侧模块；名称可调整，但职责边界必须保持：

| 建议文件 | 单一职责 |
|---|---|
| `pc_app/src/smart_neckband/health_models.py` | 与机器契约对应的不可变数据模型和 JSON 序列化 |
| `source_coordinator.py` | 在 V0 frame decode 后暂存 packet，执行 reset confirm/rollback，并按 commit/replay 语义推进 sequence/loss |
| `health_state.py` | 从 `PcDataStores`、`SerialRuntimeStatus`、`ParserStats`、`EcgAnalysisResult` 构建当前状态 |
| `health_quality.py` | 质量窗口、丢包率、clipping 比例和明确分级 |
| `health_events.py` | 事件阈值、迟滞、生命周期和 revision |
| `health_store.py` | SQLite 状态 revision、事件、Webhook outbox、审计 |
| `health_mcp.py` | 只读 MCP tools 与 transport 入口 |
| `health_webhook.py` | 签名、outbox claim、投递、重试、死信 |
| `pc_app/tests/test_health_*.py` | 单元、契约、并发、重启与错误测试 |

必须复用：

- `PcDataStores`；
- `SerialPacketReader.runtime_status`；
- `PacketParser.stats`；
- `analyze_recent_ecg()` / `EcgAnalysisResult`；
- 固件 V0 常量和 flags 的 Python 定义。

以下现有文件必须在 P0 中显式改造，不能只新增旁路 health 模块：

| 现有文件 | 必须修改的接入点 |
|---|---|
| `pc_app/src/smart_neckband/protocol.py` | 将 frame magic/length/CRC/decode 与 packet sequence/loss commit 分成两个阶段；`PacketParser.feed()` 不得在 source coordinator 决定 commit 前污染 per-source sequence/loss baseline |
| `pc_app/src/smart_neckband/serial_io.py` | 保留 parser 前的 raw recorder；为完整入站 packet 创建 receipt，先交 source coordinator；只有 coordinator 输出 committed packet 后才 append ring、更新 runtime、publish |
| `pc_app/src/smart_neckband/ble_io.py` | 保留 notification parser 前的 raw recorder；与 serial 使用同一 coordinator/receipt 路径；只有 committed `VOICE_TEXT_CHUNK` 才能进入 voice assembler、durable insert 和 ACK |
| `pc_app/src/smart_neckband/buffers.py` | ECG/status 条目保存本次接收的 monotonic/UTC evidence；`StatusSample` 不得再只有设备字段 |
| `pc_app/src/smart_neckband/analysis.py` | `EcgAnalysisResult` 绑定 source instance、analyzed-through ECG sample index 和该样本 receipt |
| `pc_app/tests/test_protocol_v0.py` | staged decode、commit/replay、wrap、candidate rollback 和 parser stats |
| `pc_app/tests/test_ble_io.py` / serial reader tests | buffered voice 不提前持久化/ACK；非 ECG packet 不刷新 ECG receipt |
| `pc_app/tests/test_analysis.py` / `test_health_*.py` | analysis provenance、worker stall、双 age、reset 与 event contract |

不得：

- 从公共 Live Web 页或 Redis snapshot 反向获取 Health MCP 权威状态；
- 在 `live_uploader.py` 中复制一套不同的 HR/SQI 算法；
- 把健康事件转成自然语言后塞入普通文本指令 FIFO；
- 复用普通 `/v1/instructions` 或 `/agent-replies` endpoint；
- 把健康 Webhook 状态表与普通 Agent reply 表混成同一去重命名空间。

### 3.1 入站 packet commit 管线

P0 必须把当前“parse 后立即 dispatch”改成以下顺序：

```text
transport bytes
→ optional raw recorder / raw_chunk_callback writes the exact chunk once
→ frame extraction + magic/length/CRC/payload decode
→ create immutable StagedPacket(raw bytes, decoded packet, PacketReceipt)
→ SourceInstanceCoordinator.ingest(staged)
   ├─ pending: buffer only
   ├─ confirmed reset: reset per-source baseline, replay buffer
   └─ rejected reset: drop trigger, replay remaining buffer on old baseline
→ PacketParser.commit_packet / sequence-loss stats exactly once
→ committed ECG passes the one per-source raw-uint32-to-ordinal extender
→ ECG/IMU/status rings or voice assembler
→ runtime evidence fields
→ publisher / durable voice insert / voice ACK
→ HealthStateBuilder / events / webhook
```

raw recorder 属于 L0 证据路径，必须保持在 frame extraction、CRC/decode、reset coordinator 和任何 commit/drop 决策之前。启用 recorder 时，每个 transport chunk 按收到的字节和顺序恰好写一次；CRC 错误、decode 错误、被拒绝的 reset trigger 和最终丢弃的 stale/duplicate packet 也必须保留。coordinator replay 不得再次写 recorder，且 recorder 失败沿用现有显式错误/停止策略，不得静默改写或筛选原始流。

`PacketReceipt` 必须由 serial 和 BLE 共用，字段至少为：

```text
received_monotonic_ns: int
received_at_utc: RFC3339 millisecond Z
```

完整 frame 从 transport buffer 提取后，立即通过可注入的 clock 一次性生成这一对值；buffer/replay 时逐字保留，不得重新取时。CRC/magic/length/decode 错误计数可以在 staged decode 阶段立即增加；`packets_ok`、sequence gap/loss、duplicate/stale 和 per-source maxima 只能在 commit 时增加，并且每个最终 committed packet 恰好一次。被丢弃的 reset trigger 只增加独立 `reset_candidate_rejected` diagnostic，不计入 `packets_ok`，也不得制造虚假 packet loss。

当前 `PacketParser.feed()` 会在返回 packet 前更新 sequence/loss，当前 BLE `_dispatch()` 会立即进入 voice durable/ACK 路径；实现者必须重构这些位置，不能在其后补一个观察器冒充 coordinator。

raw recorder 回归测试必须向 serial 和 BLE 输入包含正常 packet、坏 CRC frame、reset rejected trigger 和后续有效 packet 的同一 byte stream，断言 recorder 输出与输入逐字节完全相等，且 reset replay 不增加第二份字节。

runtime/store evidence 的最小新增字段：

| 所在对象 | 字段 |
|---|---|
| serial 与 BLE 共用的 runtime status | `last_transport_packet_monotonic_ns`、`last_transport_packet_received_at_utc` |
| 同一 runtime status | `last_ecg_packet_monotonic_ns`、`last_ecg_packet_received_at_utc`、`last_ecg_sample_ordinal`、`last_ecg_raw_sample_index` |
| ECG ring batch/sample metadata | `source_instance_id`、receipt monotonic/UTC、`raw_first_sample_index`、`first_sample_ordinal`、`last_sample_ordinal` |
| `StatusSample` | `source_instance_id`、`received_monotonic_ns`、`received_at_utc` |

一次 committed ECG 必须先经过共用 ordinal extender，再在同一个 reader lock 内更新 raw index、ordinal、monotonic 和 UTC；一次 committed transport packet 同理更新 transport pair。IMU/status/voice 只能更新 transport pair。`DEVICE_STATUS` 的 “fresh” 也基于其自己的 `StatusSample.received_monotonic_ns`：age `<=2000 ms` 才是 fresh，不得借用 ECG 或 transport 的 age。

### 3.2 P0 进程和 SQLite IPC 模型

stdio MCP 进程不能直接读取另一个 GUI 进程中的 Python 对象。P0 固定采用以下模型：

```text
唯一采集 owner（GUI 或 headless acquisition）
  ├─ 独占打开 SPP/UART/BLE
  ├─ PacketParser + PcDataStores + analysis
  ├─ 每 500 ms 构建并原子写入最新状态快照
  ├─ 处理 event state machine
  └─ claim health webhook outbox lease

MCP stdio child
  └─ 只读打开同一 SQLite，读取最新状态/event/audit
```

约束：

- 同一设备同一时刻只能有一个 acquisition owner，MCP 子进程不得再次打开 COM/BLE；
- SQLite 使用 WAL、busy timeout 和显式 migration version；
- writer 把 `evidence_received_monotonic_ns`、`snapshot_written_monotonic_ns` 和 `source_instance_id` 写入内部列；
- MCP 子进程与 writer 使用同一台 PC 的单调时钟重新计算 age；
- 如果持久化单调值大于当前单调值，说明主机重启或记录无效，必须视为 offline，不得回退到墙上时钟把旧状态恢复为 fresh；
- writer 每 500 ms 最多发布一次状态 revision；`state_revision` 每次成功 transaction 后递增；
- Webhook worker 必须持有带到期时间的 DB lease，避免 GUI 和 headless worker 重复投递；
- 没有运行 acquisition owner 时，MCP 仍可启动，但只能返回 `STATE_UNAVAILABLE` 或已有 device 的 offline 状态。

## 4. 版本、标识符和兼容策略

### 4.1 三种版本不得混用

| 名称 | P0 值 | 用途 |
|---|---|---|
| MCP protocol revision | `2025-11-25` | MCP 生命周期、JSON-RPC、transport、tools |
| Health contract version | `0.2.0` | 本文和机器契约 |
| Firmware protocol version | `1` | 固件 V0 二进制包头 |

`schema_version` 的值固定为 `0.2.0`。不得写成 `0.2`、MCP 日期或固件 `1`。

### 4.2 标识符

- `wearer_id`：1–64 字符，匹配 `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`；
- `source_instance_id`、`event_id`、`notification_id`、`trace_id`：小写 UUID 字符串；
- `state_revision`、`event_revision`、`notification_sequence`：非负、安全 JSON 整数；
- ID 不得包含真实姓名、手机号、邮箱或设备序列号。

P0 单实例只允许一个配置好的 `wearer_id`。请求其他 wearer 必须返回 `WEARER_NOT_FOUND`，不得泄漏可用 wearer 列表。

### 4.3 Schema 演进

- 所有对象默认 `additionalProperties: false`；
- 未知枚举值必须拒绝，不得静默映射为现有含义；
- 新增可选字段也必须升级契约版本并同步双方 Schema；
- 删除字段、改类型、改语义、收紧范围、改变错误分类，均视为破坏性变更；
- MCP 协议升级与 Health 契约升级分别管理；
- 变更必须同时更新本文、机器契约、golden messages 和双方契约测试。

## 5. P0 领域模型

完整 JSON Schema 位于机器契约的 `$defs`。本节给出规范语义与完整示例。

### 5.1 可用性原因

可空指标不得只返回 `null`。每个指标独立包含：

```json
{
  "value": null,
  "unit": "bpm",
  "valid": false,
  "observed_at": null,
  "age_ms": null,
  "window_s": 10.0,
  "method": "neurokit2_rr_median",
  "unavailable_reason": "insufficient_stable_r_peaks"
}
```

P0 `unavailable_reason`：

| 值 | 含义 |
|---|---|
| `not_implemented` | 本仓库尚无该算法，例如运动分类 |
| `no_data` | 尚未收到足够数据 |
| `stale_data` | 证据超过允许新鲜度 |
| `lead_off` | 电极脱落/接触标志 |
| `adc_clipping` | ADC clipping 门控 |
| `insufficient_window` | 分析窗口不足 |
| `insufficient_stable_r_peaks` | 合法、稳定 R peaks 不足 |
| `analysis_unavailable` | NeuroKit2/NumPy 未安装或分析失败 |
| `quality_below_threshold` | 工程质量门未通过 |

### 5.2 `WearerState`

```json
{
  "schema_version": "0.2.0",
  "wearer_id": "xwen",
  "state_revision": 1842,
  "source_instance_id": "ef132c67-a98f-474a-a673-4ab6ea784790",
  "data_source": "live",
  "observed_at": "2026-07-23T02:10:00.123Z",
  "device_timestamp_us": 1845023123,
  "ecg_sample_index": 923410,
  "age_ms": 120,
  "freshness": "fresh",
  "heart": {
    "analysis_source_instance_id": "ef132c67-a98f-474a-a673-4ab6ea784790",
    "analyzed_through_ecg_sample_index": 923350,
    "heart_rate": {
      "value": 92.0,
      "unit": "bpm",
      "valid": true,
      "observed_at": "2026-07-23T02:10:00.003Z",
      "age_ms": 240,
      "window_s": 10.0,
      "method": "neurokit2_rr_median",
      "unavailable_reason": null
    },
    "rr_interval": {
      "value": 652.0,
      "unit": "ms",
      "valid": true,
      "observed_at": "2026-07-23T02:10:00.003Z",
      "age_ms": 240,
      "window_s": 10.0,
      "method": "neurokit2_latest_valid_rr",
      "unavailable_reason": null
    }
  },
  "signal": {
    "quality_score": 0.86,
    "quality_level": "good",
    "quality_rank": 3,
    "lead_off": false,
    "adc_clipping_ratio_10s": 0.0,
    "packet_loss_ratio_10s": 0.002,
    "crc_errors_delta_10s": 0,
    "window_s": 10.0
  },
  "motion": {
    "level": null,
    "confidence": null,
    "observed_at": null,
    "age_ms": null,
    "window_s": null,
    "method": null,
    "unavailable_reason": "not_implemented"
  },
  "device": {
    "status": "ok",
    "connection_state": "RECEIVING",
    "transport": "spp",
    "firmware_protocol_version": 1,
    "ecg_sample_rate_hz": 500,
    "imu_sample_rate_hz": 50,
    "last_ecg_packet_age_ms": 120,
    "last_transport_packet_age_ms": 35,
    "sampling_active": true,
    "imu_online": true,
    "oled_online": true,
    "transport_connected": true,
    "transport_congested": false,
    "counters": {
      "packets_ok": 46191,
      "packets_lost": 9,
      "crc_errors": 0,
      "firmware_error_count": 0,
      "ecg_ring_overflow_count": 0,
      "imu_ring_overflow_count": 0,
      "transport_queue_overflow_count": 0,
      "transport_drop_count": 0,
      "i2c_error_count": 0
    }
  },
  "active_events": [],
  "test_mode": false
}
```

约束：

- `data_source` 为 `live | replay | synthetic`；
- 只有 `live` 且 `test_mode=false` 才允许生产 Webhook；
- `replay` 和 `synthetic` 始终保留在 MCP 响应中供测试，但不得伪装成实时数据；
- `motion` 在 P0 必须返回 `unavailable_reason=not_implemented`，不得从原始 IMU 直接猜测 `still`；
- `active_events` 是 `EventRef[]`，不是类型字符串数组。
- `WearerState.age_ms` 必须等于构建该 revision 时的 `device.last_ecg_packet_age_ms`；`meta.age_ms` 对 current-state 成功响应也使用同一 ECG age；
- 嵌入 `WearerState` 时两个 device age 都必须非 null，因为一个有效 ECG 同时也是入站 transport packet；独立 `get_device_status` 的 `DeviceState` 才允许其中任一为 null；
- `device.last_transport_packet_age_ms` 只描述链路有入站流量，不能使 HR/RR 重新有效，也不能解除 `input_stale` / `input_offline`。

HR/RR 只有同时满足以下条件才可 `valid=true`：

- `freshness=fresh`；
- `EcgAnalysisResult.source_instance_id == WearerState.source_instance_id`；
- `analyzed_through_ecg_sample_index <= WearerState.ecg_sample_index`，且该 analyzed-through 样本仍存在于当前 source ring；
- 指标自己的 `age_ms <= 2000`，其 age 基于 analyzed-through ECG 样本的 immutable receipt，而不是最新 ECG 或分析完成时间；
- `window_s == 10.0`；
- `quality_rank >= 2`；
- lead-off 为假；
- clipping 未触发 bad；
- `EcgAnalysisResult.message == "ok"`；
- 具体值非空且在 P0 算法有效范围内。

`EcgAnalysisResult` 必须新增并由 analysis worker 随结果原子发布：

```text
source_instance_id
analyzed_through_ecg_sample_index
analyzed_through_received_monotonic_ns
analyzed_through_received_at_utc
```

builder 看到 source mismatch、sample index 超前/已不在 ring、receipt 缺失或分析证据 age `>2000 ms` 时，必须把 HR/RR 设为 `valid=false`、`value=null`、`unavailable_reason=stale_data`。这条规则独立于 `WearerState.freshness`：即使 ECG 继续到达并保持 state fresh，analysis worker 卡死也会在最后分析证据超过 2 秒后使旧 HR/RR 失效。

失效原因优先级固定为：

```text
lead_off
→ adc_clipping
→ stale_data
→ insufficient_window
→ insufficient_stable_r_peaks
→ analysis_unavailable
→ quality_below_threshold
```

### 5.3 质量分级

质量顺序固定为：

```text
unknown(0) < bad(1) < fair(2) < good(3)
```

不得直接对枚举字符串使用 `>=`。比较时只能使用 `quality_rank` 或固定映射。

P0 每 500 ms 评估一次最近 10 秒窗口：

1. 最新 lead-off 为真时，`bad`；
2. `adc_clipping_ratio_10s >= 0.80` 时，`bad`；
3. 证据 stale/offline 时，`unknown`，相应生理指标无效；
4. SQI 缺失时，`unknown`；
5. SQI `< 0.50` 时，`bad`；
6. SQI `[0.50, 0.80)` 时，`fair`；
7. SQI `>= 0.80` 时，仅当 clipping ratio `< 0.05`、10 秒 packet loss ratio `<= 0.01`、CRC delta 为 `0` 时为 `good`；
8. 上一步任何附加条件不满足但未触发 `bad` 时为 `fair`。

计算定义：

```text
adc_clipping_ratio_10s =
  clipping_flagged_ecg_samples / ecg_samples_in_window

packet_loss_ratio_10s =
  packets_lost_delta / (packets_ok_delta + packets_lost_delta)
```

分母为 0 时对应值为 `null`，质量不得因此升级为 `good`。这些阈值只用于工程门控，必须在 UI 和日志中标为“prototype signal quality”。

### 5.4 设备状态推导

`DeviceState.status` 固定按以下优先级推导：

1. reader closed/error、`last_transport_packet_age_ms > 10000`，或从 reader start 起超过 10 秒仍未收到任何入站 packet：`offline`；
2. reader 正常且 `2000 < last_transport_packet_age_ms <= 10000`：`stale`；
3. reader start 后 10 秒内尚未收到任何 packet，或 transport fresh 但 ECG `last_ecg_packet_age_ms > 2000` / 为 `null`、sampling 不 active、transport congested、IMU offline、lead-off、clipping，或最近 10 秒出现新增 overflow/error：`degraded`；
4. transport 和 ECG 都 fresh 且无上述降级证据：`ok`。

这里只有 `last_transport_packet_age_ms` 可以推导 device `stale/offline`。ECG age 只能把 transport-fresh 的 device 降为 `degraded`，不得把仍在持续发送 voice/status 的设备标成 transport offline。

transport 映射：

- BLE reader：`ble`；
- serial reader 且选择的 `SerialPortInfo.is_bluetooth_candidate=true`：`spp`；
- 其他 serial reader：`uart`；
- 无法识别：`unknown`。

状态 bit 尚未收到时，相应 boolean 为 `null`，不得默认写成正常。`SignalState.lead_off` 同样为 `boolean|null`；未收到 fresh `DEVICE_STATUS` 时为 `null`，quality 必须为 `unknown`，不能解释为“电极连接正常”。

### 5.5 `WearerEvent`

P0 事件类型只有：

```text
lead_off
adc_clipping
input_stale
input_offline
```

`high_hr_low_motion`、`poor_contact`、`user_started_moving`、`user_stopped`、HRV 或姿态事件不属于 `0.2.0` 枚举。未来新增前必须先实现并验证相应算法，再升级契约。

完整事件示例：

```json
{
  "schema_version": "0.2.0",
  "event_id": "50d40557-8df6-47b5-abce-1ef447bf5543",
  "event_revision": 2,
  "event_type": "lead_off",
  "wearer_id": "xwen",
  "source_instance_id": "ef132c67-a98f-474a-a673-4ab6ea784790",
  "data_source": "live",
  "status": "resolved",
  "severity": "warning",
  "opened_at": "2026-07-23T02:09:40.000Z",
  "updated_at": "2026-07-23T02:10:03.000Z",
  "resolved_at": "2026-07-23T02:10:03.000Z",
  "state_revision": 1849,
  "evidence": {
    "lead_off": false,
    "adc_clipping_ratio_10s": null,
    "last_ecg_packet_age_ms": 110,
    "last_transport_packet_age_ms": 35,
    "quality_level": "fair",
    "detail": "Three consecutive fresh DEVICE_STATUS packets reported both lead-off bits clear."
  },
  "recommended_consumer_behavior": [
    "refresh_current_state",
    "do_not_infer_diagnosis"
  ],
  "test_mode": false
}
```

所有 event transition 的前置条件是：同一 wearer 已有一个将与 transition 同 transaction 持久化的 `WearerState` revision。首次有效 ECG 之前：

- 可以通过 `health.get_device_status` 返回 transport/device snapshot；
- 可以在内存/status ring 中保留 fresh `DEVICE_STATUS` evidence；
- 不得打开 `lead_off` 或任何其他 Health event；
- 不得分配 event ID、`state_revision` 或 Webhook outbox。

首次 ECG 到达并创建 `WearerState` revision `R` 时，event state machine 必须在同一 transaction 中重新评估此前保留且仍 fresh 的 status evidence；若 lead-off 为真，立即以 `state_revision=R` 打开事件并创建 outbox。这样不引入虚构的 device-snapshot revision，也不会生成无法由 MCP 查询到的 event 引用。

事件生命周期：

- `event_id` 标识一次逻辑 episode；
- `event_revision` 从 `1` 开始，每次状态变化递增；
- `status` 只有 `active | resolved`；
- 同类型 active 事件对同一 wearer 只能有一个；
- 事件打开时生成新 `event_id`；
- resolved 事件再次满足打开条件时必须生成新的 `event_id`；
- 事件和相应 Webhook outbox 必须在同一 SQLite transaction 中写入；
- 不得因应用重启重复打开仍 active 的相同事件。

### 5.6 P0 事件算法

#### `lead_off`

- 打开：已经有本次 transaction 的 `WearerState` revision，且最新 fresh `DEVICE_STATUS.lead_off_flags` 任一 lead-off bit 为真；无需额外延迟；
- 维持：lead-off 为真，或没有足够 fresh 的 status 证明恢复；
- 解除：连续 3 个 fresh、约 1 Hz 的 `DEVICE_STATUS` 都显示两个 lead-off bit 为假；
- severity：`warning`。

#### `adc_clipping`

- 窗口：完整 10 秒、至少 5000 个 ECG 样本；
- 打开：窗口 clipping ratio `>= 0.80`；
- 维持：ratio `>= 0.20`，或数据 stale；
- 解除：ratio `< 0.20` 持续 10 秒且数据 fresh；
- severity：`warning`。

#### `input_stale`

- 打开：最后有效 `ECG_BATCH` 的 `last_ecg_packet_age_ms > 2000` 且 `<= 10000`；
- 维持：处于同一区间；
- 解除：有效 ECG fresh 持续 1 秒；或转入 `input_offline` 时以 resolved 结束；
- severity：`warning`。

#### `input_offline`

- 打开：已经至少收到一个 ECG 的 reader 出现 `last_ecg_packet_age_ms > 10000`、reader 关闭或 reader error；
- 维持：上述任一条件存在；
- 解除：reader 正常且有效 ECG fresh 持续 1 秒；
- severity：`error`。

IMU、`DEVICE_STATUS`、`VOICE_TEXT_CHUNK`、`VOICE_STATUS` 或未来任何非 ECG 入站 packet 都不得打开、维持解除倒计时、解除或阻止这两个事件。必须测试“上述非 ECG 流量持续到达，而 ECG 停止超过 2 秒和 10 秒”时依次产生 `input_stale`、`input_offline`。

所有计时必须使用单调时钟。墙上时钟跳变不得打开、解除或延迟事件。

`input_stale → input_offline` 是一个不可拆分的复合 transition：

1. builder 先生成一个 offline `WearerState`，只分配一个新的 `state_revision=R`；
2. 在同一 SQLite transaction 内，先把 active `input_stale` 改为 resolved（其 `event_revision + 1`，引用 `state_revision=R`），再创建 active `input_offline`（新 `event_id`、`event_revision=1`，同样引用 `R`）；
3. 同一 transaction 内先写 stale-resolved outbox，分配 `notification_sequence=N`，再写 offline-opened outbox，分配 `N+1`；
4. 两个通知使用相同的 `occurred_at` 和 `trace_id`，但使用不同 `notification_id`；
5. per-wearer dispatcher 必须阻塞 `N+1`，直到 `N` 成功 ACK 或进入 dead letter，确保发送端顺序；
6. transaction 任一步失败必须全部 rollback，不得出现 state 已 offline 但事件/通知只完成一半。

## 6. MCP Server

### 6.1 P0 transport：stdio

P0 使用 stdio，由 MCP Client 启动 PC 侧进程：

```text
py -3.12 -m smart_neckband.health_mcp --transport stdio
```

要求：

- `stdout` 只写 UTF-8 MCP JSON-RPC 消息；
- 日志只写 `stderr`；
- 进程读取同一 PC Health state/store；
- 不通过 stdio 传输连续 ECG；
- tools 全部标注 `readOnlyHint=true`、`destructiveHint=false`、`idempotentHint=true`、`openWorldHint=false`；
- P0 不声明 prompts、resources 或 sampling 能力。

### 6.2 P1 transport：loopback Streamable HTTP

P1 可以增加：

```text
http://127.0.0.1:8765/mcp
```

必须满足 [MCP 2025-11-25 Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)：

- 同一路径支持 POST 和 GET；
- POST `Accept` 同时包含 `application/json` 和 `text/event-stream`；
- 初始化后使用协商得到的 `MCP-Protocol-Version`；
- 校验 `Origin`，非法 Origin 返回 `403`；
- 默认只绑定 `127.0.0.1`，不得默认绑定 `0.0.0.0`；
- 不支持 GET SSE 时明确返回 `405`；
- 不把 HTTP 202 ACK 与工具成功结果混淆。

局域网或互联网 MCP 不属于 P0/P1。要开放远程 MCP，必须另立方案，至少具备 HTTPS、MCP 授权发现、audience/resource binding、401/403 语义、token 轮换和撤销；不得把“明文 HTTP + 静态 Bearer”描述为完整 MCP OAuth。

### 6.3 P0 tools

工具名固定为：

| 工具 | 用途 |
|---|---|
| `health.get_current_state` | 获取最新完整状态；健康通知后的首选查询 |
| `health.get_event_details` | 按 `event_id` 获取权威事件 |
| `health.get_recent_events` | 分页查询事件历史 |
| `health.get_device_status` | 不依赖 HR/SQI，单独查询连接和设备状态 |

不提供重复的 `get_latest_summary`。不提供 P0 HRV、motion、posture 或 raw ECG tools。

`tools/list` 中每个工具的完整 `inputSchema` 和 `outputSchema` 由机器契约 `x-mcp-tools` 数组生成。运行时必须把内部 `$ref` 完整解析或随 `$defs` 一起返回，客户端不得依赖访问本地文件来解析 Schema。

### 6.4 公共工具结果包络

成功的 `structuredContent`：

```json
{
  "ok": true,
  "data": {},
  "meta": {
    "schema_version": "0.2.0",
    "generated_at": "2026-07-23T02:10:00.250Z",
    "data_source": "live",
    "age_ms": 120,
    "trace_id": "7a916c4a-3b3e-4ec5-8491-e5fc7e843863"
  },
  "error": null
}
```

领域失败的 `structuredContent`：

```json
{
  "ok": false,
  "data": null,
  "meta": {
    "schema_version": "0.2.0",
    "generated_at": "2026-07-23T02:10:02.250Z",
    "data_source": "live",
    "age_ms": 2120,
    "trace_id": "7a916c4a-3b3e-4ec5-8491-e5fc7e843863"
  },
  "error": {
    "code": "STATE_STALE",
    "message": "Latest wearer state is older than max_age_ms.",
    "retryable": true,
    "retry_after_ms": 500,
    "details": {
      "actual_age_ms": 2120,
      "max_age_ms": 2000
    }
  }
}
```

`ToolMeta` 不能由 SDK 临时猜测。`generated_at` 是本次响应 UTC，`trace_id` 是本次 call UUID；`data_source` 和 `age_ms` 固定如下：

| tool / 结果 | `meta.data_source` | `meta.age_ms` |
|---|---|---:|
| `get_current_state` success | 返回 state 的 source | state 的 ECG age；必须等于 `state.age_ms` 和 `state.device.last_ecg_packet_age_ms` |
| `get_device_status` success | device snapshot 所属 source | `device.last_transport_packet_age_ms`，未收到任何 transport packet 时为 `null` |
| `get_event_details` success | 该 event 的 source | `null`；历史事件的新旧由 `updated_at` 表示 |
| `get_recent_events` success，非空且全部同 source | 该 source | `null` |
| `get_recent_events` success，空结果或包含多个 source | `null` | `null` |
| `STATE_STALE` / `STATE_OFFLINE` | 最近 state 的 source | 触发失败的 ECG age |
| `DEVICE_OFFLINE` | 已有 device snapshot 的 source，否则 `null` | transport age；reader closed/error 且无可用 age 时为 `null` |
| `INVALID_ARGUMENT`、`WEARER_NOT_FOUND`、`STATE_UNAVAILABLE`、`EVENT_NOT_FOUND`、`RATE_LIMITED`、`INTERNAL_ERROR` | `null` | `null` |

失败响应不得为了填 meta 而访问本应跳过的数据源；例如参数验证失败不查询 wearer。机器契约 `x-tool-meta-semantics` 和 `x-golden.tool_meta_*` 冻结这些映射。

MCP 结果必须同时满足：

- 成功：`result.isError=false`；
- 领域/业务失败：`result.isError=true`；
- `result.structuredContent` 放置上述包络；
- `result.content` 包含且只包含一个 `TextContent`，其 `text` 是同一包络的紧凑 JSON；
- `content[].text` 解析后的 JSON 必须与 `structuredContent` 深度相等；
- 如果 tool 声明 `outputSchema`，`structuredContent` 必须通过校验。

### 6.5 `tools/call` 成功报文的位置与结构

请求：

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": {
    "name": "health.get_current_state",
    "arguments": {
      "wearer_id": "xwen",
      "max_age_ms": 2000
    }
  }
}
```

响应的结构固定为：

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"ok\":true,\"data\":{\"state\":\"<与 structuredContent.data 相同的完整 WearerState>\"},\"meta\":{\"schema_version\":\"0.2.0\",\"generated_at\":\"2026-07-23T02:10:00.250Z\",\"data_source\":\"live\",\"age_ms\":120,\"trace_id\":\"7a916c4a-3b3e-4ec5-8491-e5fc7e843863\"},\"error\":null}"
      }
    ],
    "structuredContent": {
      "ok": true,
      "data": {
        "state": "<完整 WearerState 对象，不能是字符串>"
      },
      "meta": {
        "schema_version": "0.2.0",
        "generated_at": "2026-07-23T02:10:00.250Z",
        "data_source": "live",
        "age_ms": 120,
        "trace_id": "7a916c4a-3b3e-4ec5-8491-e5fc7e843863"
      },
      "error": null
    },
    "isError": false
  }
}
```

上例是位置示意。机器契约同时提供字段完整的 `x-golden.mcp_get_current_state_success_structured_content` 和可直接执行的完整 JSON-RPC `x-golden.mcp_get_current_state_success`；实现测试必须验证 `content[0].text` 解析后与 `structuredContent` 深度相等。

### 6.6 完整领域失败报文

```json
{
  "jsonrpc": "2.0",
  "id": 8,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"ok\":false,\"data\":null,\"meta\":{\"schema_version\":\"0.2.0\",\"generated_at\":\"2026-07-23T02:10:02.250Z\",\"data_source\":\"live\",\"age_ms\":2120,\"trace_id\":\"7a916c4a-3b3e-4ec5-8491-e5fc7e843863\"},\"error\":{\"code\":\"STATE_STALE\",\"message\":\"Latest wearer state is older than max_age_ms.\",\"retryable\":true,\"retry_after_ms\":500,\"details\":{\"actual_age_ms\":2120,\"max_age_ms\":2000}}}"
      }
    ],
    "structuredContent": {
      "ok": false,
      "data": null,
      "meta": {
        "schema_version": "0.2.0",
        "generated_at": "2026-07-23T02:10:02.250Z",
        "data_source": "live",
        "age_ms": 2120,
        "trace_id": "7a916c4a-3b3e-4ec5-8491-e5fc7e843863"
      },
      "error": {
        "code": "STATE_STALE",
        "message": "Latest wearer state is older than max_age_ms.",
        "retryable": true,
        "retry_after_ms": 500,
        "details": {
          "actual_age_ms": 2120,
          "max_age_ms": 2000
        }
      }
    },
    "isError": true
  }
}
```

可执行版本位于机器契约 `x-golden.mcp_state_stale_error`。`x-golden.mcp_state_offline_error` 冻结 ECG offline、transport 仍可用的独立领域失败；`x-golden.mcp_unknown_tool_error` 和 `x-golden.mcp_malformed_call_error` 分别冻结 unknown tool 与缺少 tool name 的 JSON-RPC error。

### 6.7 JSON-RPC error 与 tool execution error

遵循 [MCP Tools 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)：

| 情况 | 返回方式 |
|---|---|
| JSON-RPC 非法、method 不存在、`tools/call` 结构不合法 | JSON-RPC `error` |
| 工具不存在 | JSON-RPC `error` |
| 参数字段合法但值越界/语义无效 | tool result，`isError=true`，`INVALID_ARGUMENT` |
| wearer 不存在、状态 stale/offline、事件不存在 | tool result，`isError=true` |
| 限流 | tool result，`isError=true`，`RATE_LIMITED` |
| 内部状态库/分析错误 | tool result，`isError=true`，`INTERNAL_ERROR` |

P0 错误码：

| code | retryable | 客户端行为 |
|---|---:|---|
| `INVALID_ARGUMENT` | false | 修正参数，不原样重试 |
| `WEARER_NOT_FOUND` | false | 停止该 wearer 查询 |
| `STATE_UNAVAILABLE` | true | 等待首个有效包 |
| `STATE_STALE` | true | 不使用旧生理值，稍后查询 |
| `STATE_OFFLINE` | true | ECG 生理输入超过 10 秒未更新；transport 可能仍在线 |
| `DEVICE_OFFLINE` | true | reader/transport 真正离线，不使用缓存替代 |
| `EVENT_NOT_FOUND` | false | 记录通知/存储不一致 |
| `RATE_LIMITED` | true | 遵守 `retry_after_ms` |
| `INTERNAL_ERROR` | true | 记录 trace ID，指数退避 |

`SIGNAL_UNRELIABLE` 不作为 `get_current_state` 整体失败：服务仍返回 device/signal 状态，但把具体 HR/RR 指标标为无效并给出各自原因。

### 6.8 工具语义

#### `health.get_current_state`

输入：

```json
{
  "wearer_id": "xwen",
  "max_age_ms": 2000
}
```

- `max_age_ms`：`100..2000`，默认 `2000`；它只能收紧 2 秒 fresh 门槛；
- 从未收到有效状态：`STATE_UNAVAILABLE`；
- reader closed/error 或 `last_transport_packet_age_ms > 10000`：`DEVICE_OFFLINE`；
- transport 仍正常，但 ECG age `>10000`：`STATE_OFFLINE`；
- ECG age 超过 `max_age_ms` 且 `<=10000`：`STATE_STALE`；
- 成功数据：`{"state": WearerState}`。

上述判断按列出顺序执行。持续 voice/status 使 transport fresh 时，只会命中 `STATE_OFFLINE`，不得命中 `DEVICE_OFFLINE`。

#### `health.get_event_details`

输入：

```json
{
  "event_id": "50d40557-8df6-47b5-abce-1ef447bf5543"
}
```

成功数据：`{"event": WearerEvent}`。不存在返回 `EVENT_NOT_FOUND`。

#### `health.get_recent_events`

输入：

```json
{
  "wearer_id": "xwen",
  "event_types": ["lead_off", "input_offline"],
  "statuses": ["active", "resolved"],
  "since": "2026-07-22T02:10:00.000Z",
  "cursor": null,
  "limit": 20
}
```

规则：

- `limit`：`1..100`，默认 `20`；
- 排序：`updated_at DESC, event_id DESC`；
- `cursor` 是不透明、URL-safe Base64 字符串，编码最后一条的排序键和查询摘要；
- cursor 必须与原筛选条件绑定，换筛选条件复用 cursor 返回 `INVALID_ARGUMENT`；
- 输出：`{"events": WearerEvent[], "next_cursor": string|null}`。

#### `health.get_device_status`

输入：

```json
{
  "wearer_id": "xwen"
}
```

成功数据：`{"device": DeviceState}`。本工具在 HR/SQI 不可用时仍可成功；如果 reader offline，返回成功的 `device.status=offline`，便于诊断。配置中没有该 wearer/device、且从未建立 reader 或 device snapshot 时才返回 `STATE_UNAVAILABLE`；已启动 reader 但尚无 packet 时返回 degraded/offline DeviceState，并允许两个 age 为 null。

### 6.9 限流

stdio P0 每个 server process 计数；HTTP P1 按认证主体（无认证时按 loopback process/session）、tool 和 wearer 组合计数。

| 工具 | 滑动 60 秒上限 |
|---|---:|
| `health.get_current_state` | 120 |
| `health.get_event_details` | 120 |
| `health.get_recent_events` | 30 |
| `health.get_device_status` | 60 |

超限返回 `RATE_LIMITED`，`retry_after_ms` 为下一次允许时间。不得把“建议最小调用间隔”只写在文档而不由服务端执行。

## 7. 健康事件 Webhook

### 7.1 定位

Webhook 是 **at-least-once 唤醒通知**，不是权威健康数据。接收方持久化并 ACK 后，应调用：

1. `health.get_event_details(event_id)`；
2. `health.get_current_state(wearer_id, max_age_ms=2000)`；
3. 校验 `data_source`、`test_mode`、freshness、事件 revision；
4. 再由接收方自己的确定性策略处理。

本仓库不定义也不保证机器人动作。接收方不得只根据 Webhook body 驱动物理运动。

### 7.2 独立 endpoint

```http
POST /v1/health-events
```

不得复用：

```text
/v1/instructions
/agent-replies
```

本地 mock receiver 可以使用：

```text
http://127.0.0.1:<port>/v1/health-events
```

非 loopback 必须使用 HTTPS。HTTP URL 的 host 若不是 `127.0.0.1`、`[::1]` 或 `localhost`，发送端配置校验必须失败。

### 7.3 请求头

```http
Content-Type: application/json; charset=utf-8
Accept: application/json
User-Agent: smart-neckband-health/0.2.0
X-Smart-Collar-Key-Id: health-webhook-2026-07
X-Smart-Collar-Timestamp: 1784772600
X-Smart-Collar-Notification-Id: 894d7ebf-3c7a-4818-a85d-3555a0d4dd13
X-Smart-Collar-Signature: v1=6d9f...
```

`Accept: application/json` 和 `User-Agent` 是 sender advisory headers：sender 应发送，receiver 不得要求它们存在，也不得因其缺失或包含其他值拒绝请求。接收端响应格式由本契约固定，不通过请求 `Accept` 协商。`Content-Type`、`Content-Length` 和 4 个 `X-Smart-Collar-*` 验证头才是 receiver-required headers。

签名算法：

```text
signed_payload = ascii(timestamp) + "." + raw_utf8_request_body
signature = lowercase_hex(HMAC-SHA256(secret, signed_payload))
header = "v1=" + signature
```

要求：

- P0 `secret` 是恰好 32 个随机 bytes；配置层只接受其 64 字符 lowercase hex 编码；
- 比较签名必须 constant-time；
- 验签必须基于收到的原始 body bytes，不得重新序列化 JSON；
- receiver 允许的时间偏差为 `±300` 秒；
- `Key-Id` 用于轮换，至少允许当前和前一个 secret 并存；
- 日志不得打印 secret、完整签名或完整 body；
- health secret 不得与 MCP、Live Web、普通 Agent Gateway token 共用。

唯一配置格式：

```text
SMART_COLLAR_HEALTH_WEBHOOK_SECRET_HEX =
  ^[0-9a-f]{64}$

secret_bytes = hex_decode(SMART_COLLAR_HEALTH_WEBHOOK_SECRET_HEX)
```

不接受 `0x` 前缀、uppercase、空白、换行、奇数长度、Base64 或直接 UTF-8 passphrase。缺失或不匹配正则、解码失败、或解码结果不是 32 bytes 时，sender/receiver 必须在启动配置校验阶段 fail closed：不得启动 delivery/receiver worker，不得发出网络请求；日志只记录变量名和错误类别，不回显值。轮换时当前/前一 key ID 分别绑定一份同格式的 32-byte secret。

Header 和 body 的一致性规则：

- `X-Smart-Collar-Notification-Id` 必须与 body `notification_id` 逐字符相等，否则返回 `400 invalid_request`；
- `X-Smart-Collar-Timestamp` 必须匹配 `^[1-9][0-9]{0,11}$`，按十进制 Unix seconds 解析，不允许符号、小数、空格或前导零；
- 允许的数值范围是 `1..253402300799`，之后再检查与 receiver 当前 UTC 的 `±300` 秒偏差；
- signature header 只接受一个 `v1=<64 lowercase hex>` 值。

### 7.4 请求体

```json
{
  "schema_version": "0.2.0",
  "notification_id": "894d7ebf-3c7a-4818-a85d-3555a0d4dd13",
  "notification_sequence": 431,
  "event_id": "50d40557-8df6-47b5-abce-1ef447bf5543",
  "event_revision": 2,
  "transition": "resolved",
  "event_type": "lead_off",
  "wearer_id": "xwen",
  "source_instance_id": "ef132c67-a98f-474a-a673-4ab6ea784790",
  "state_revision": 1849,
  "data_source": "live",
  "occurred_at": "2026-07-23T02:10:03.000Z",
  "sent_at": "2026-07-23T02:10:03.120Z",
  "trace_id": "7a916c4a-3b3e-4ec5-8491-e5fc7e843863",
  "test_mode": false
}
```

Webhook 不包含 HR、RR、SQI、原始 ECG、cleaned ECG、R peaks 或诊断文本。

首次创建 outbox 时，sender 必须生成并持久化 **raw UTF-8 body bytes**，之后不得从数据库字段重新序列化。P0 serializer 固定为：

- UTF-8，无 BOM；
- key 顺序严格等于 7.4 示例和 `WebhookRequest.required` 顺序；
- key 与 value 之间使用 `:`，成员之间使用 `,`，不加入任何空白；
- boolean 使用小写 `true` / `false`；
- 字符串只按 JSON 要求转义 quote、backslash 和 U+0000..U+001F；Schema 限制的所有当前字段值均为 ASCII；
- body 末尾没有换行。

机器契约 `x-golden.webhook_canonical_body_utf8` 是该 serializer 的逐字节 golden。

### 7.5 ACK

receiver 必须在以下动作完成后 ACK：

1. 按顺序验证 method、path、Content-Type、Content-Length 和 body 大小；
2. 验证时间戳，再对尚未解析的 raw body bytes 验证 HMAC；
3. 将 raw body 按 UTF-8 解析为 JSON，并校验 `WebhookRequest` Schema；
4. 验证 header 中的 notification ID 与 body 中的 `notification_id` 逐字相等；
5. 在一个数据库 transaction 中按 `notification_id` 唯一键执行 insert-or-read-digest；
6. 同一 transaction 中只为首次插入的通知写入独立健康队列；commit 完成后才 ACK。

校验顺序是规范的一部分。命中一项失败后必须立即返回对应错误，不得继续到后面的验签、解析、去重或写库步骤。`Content-Type` 的 media type 大小写不敏感，只接受：

- `application/json`；
- `application/json; charset=utf-8`，其中参数名和 charset 值大小写不敏感、分号两侧可有 OWS。

不得接受其他 charset、其他参数或多个参数。P0 receiver 不接受 chunked transfer；必须有十进制 `Content-Length`，范围为 `1..65536`，并在累计读取超过 65536 bytes 时立即拒绝。

新通知：

```http
HTTP/1.1 202 Accepted
Content-Type: application/json; charset=utf-8
```

```json
{
  "notification_id": "894d7ebf-3c7a-4818-a85d-3555a0d4dd13",
  "status": "accepted"
}
```

相同 ID、相同 raw body digest 的重复通知：

```json
{
  "notification_id": "894d7ebf-3c7a-4818-a85d-3555a0d4dd13",
  "status": "duplicate"
}
```

仍返回 `202`。receiver 必须持久化 `SHA-256(raw body bytes)`：

- 相同 `notification_id` 且 digest 相同：duplicate；
- 相同 `notification_id` 但 digest 不同：`409 notification_id_conflict`；
- 不得用解析后的 JSON 深度相等、字段子集或重新序列化结果代替 raw-body digest。

receiver 的持久化必须具备数据库级原子幂等，不得实现为“无锁 SELECT 后再 INSERT”：

```text
health_notifications
  notification_id  PRIMARY KEY / UNIQUE
  raw_body_sha256  NOT NULL
  raw_body          NOT NULL
  received_at       NOT NULL

BEGIN IMMEDIATE（或其他数据库的等价唯一约束 transaction）
  INSERT notification_id + digest + raw body ON CONFLICT DO NOTHING
  if inserted:
      INSERT health_queue exactly once
      outcome = accepted
  else:
      SELECT raw_body_sha256 for notification_id in this transaction
      outcome = duplicate when digest equal, otherwise conflict
COMMIT
```

同 ID/同 digest 并发请求必须恰好一个 `accepted`，其余为 `duplicate`；同 ID/不同 digest 并发请求必须恰好一个首次值被接受，其余不同值返回 `409`。queue insert 与 notification insert 同 transaction，进程在 DB commit 后、HTTP ACK 前崩溃时，重试必须得到 duplicate 且不能重复入队。transaction/unique-check 失败返回 `503 internal_error`。

ACK 只表示已持久化，不表示 Agent 已完成查询、判断或外部动作。

### 7.6 HTTP 状态与发送方分类

所有失败响应必须使用 `Content-Type: application/json; charset=utf-8`，body 必须精确匹配机器契约的 `WebhookErrorAck`：

```json
{
  "error": "invalid_request"
}
```

receiver 的验证失败映射固定如下；实现不得把多个情况折叠成临时选择的状态码：

| 按序条件 | HTTP | `error` | 发送方分类 |
|---|---:|---|---|
| method 不是 `POST` | `405` | `method_not_allowed` | terminal |
| path 不精确等于 `/v1/health-events` | `404` | `not_found` | terminal |
| Content-Type 不符合 7.5 | `415` | `unsupported_media_type` | terminal |
| Content-Length 缺失、不是十进制整数、为 0，或使用 chunked transfer | `400` | `invalid_request` | terminal |
| 声明或实际 raw body 超过 65536 bytes | `413` | `body_too_large` | terminal |
| timestamp 缺失、格式非法、超出整数范围，或与 receiver UTC 相差超过 `±300` 秒 | `401` | `timestamp_out_of_range` | terminal |
| key ID/signature 缺失、key ID 未知、签名语法非法，或常量时间 HMAC 比较失败 | `401` | `invalid_signature` | terminal |
| raw body 不是合法 UTF-8 JSON，或不满足 `WebhookRequest` Schema | `400` | `invalid_request` | terminal |
| header notification ID 与 body `notification_id` 不逐字相等 | `400` | `invalid_request` | terminal |
| 已存在相同 notification ID，但 raw-body SHA-256 不同 | `409` | `notification_id_conflict` | terminal |
| 已验证通知无法完成持久化或幂等检查 | `503` | `internal_error` | retryable |

`405` 响应还必须返回 `Allow: POST`。除 `405` 的 `Allow` 外，失败响应不得回显 secret、签名、raw body、解析后的通知或内部异常。P0 receiver 自身不产生 `403`；反向代理或上游访问控制可以产生 `403`，发送方仍按 terminal 处理。

| HTTP/结果 | 发送方处理 |
|---|---|
| `202` 且 ACK Schema、ID 正确 | 成功，完成 outbox item |
| `400` | terminal；契约/配置错误，进入 dead letter |
| `401` / `403` | terminal；停止自动投递并高优先级告警 |
| `404` / `405` | terminal；endpoint 配置错误 |
| `413` / `415` | terminal；body 或 Content-Type 配置错误 |
| `409 notification_id_conflict` | terminal；数据完整性告警 |
| `300..399` | terminal；redirect 被禁止，忽略 `Location`，不得访问跳转目标 |
| `408` / `425` / `429` | retryable；尊重 `Retry-After` |
| `500..599` | retryable |
| DNS、连接、TLS、timeout、无效 ACK JSON | retryable |
| 其他 `2xx` | terminal；不得把不符合契约的响应当成功 |

P0 HTTP client 必须关闭自动 redirect，包括 `301`、`302`、`303`、`307` 和 `308`；即使 `Location` 同 host 或使用 HTTPS 也不得跟随。每次投递只允许访问经过 7.2 URL policy 校验的原始配置 URL，防止签名头和 raw body 被转发到未验证目标。

### 7.7 timeout 与重试

- 单次 HTTP 请求 timeout：3 秒；
- 第一次失败后延迟：`1, 2, 4, 8, 16, 30, 60` 秒；
- 之后每 300 秒重试；
- 每次加入 `±20%` jitter；
- `Retry-After` 合法时取 `max(本地退避, Retry-After)`，但不超过 1 小时；
- 从首次尝试起超过 24 小时仍失败，进入 dead letter；
- 应用重启后必须从 SQLite outbox 恢复；
- 每次重试逐字节复用首次持久化的 raw body，包括固定不变的 `sent_at`；只更新签名请求头时间戳并基于该时间戳重新计算 HMAC；
- 不得在内存 ACK 前删除 outbox item。

### 7.8 重复、乱序与并发

- 语义为 at-least-once，不保证单次投递；
- `notification_sequence` 按 wearer 持久化严格递增；
- receiver 用 7.5 的唯一索引 transaction 原子去重、落库和入队，不存在独立的“先查”窗口；
- 乱序通知仍可持久化和 ACK，因为最终状态以 MCP 查询为准；
- receiver 不得因旧 notification 覆盖较新的本地处理状态；
- 同一 `event_id` 的 `event_revision` 小于已处理 revision 时，只记录审计，不重复触发业务；
- 同一事件不同 revision 可以并存；
- Webhook worker 与普通 Agent instruction worker 必须使用不同队列和线程/进程池；
- 健康通知不得被长时间 LLM 回合阻塞。

## 8. 消费方最低安全契约

本节只定义消费要求，不宣称本仓库实现了消费方。

接收通知后的最小流程：

```text
verify HTTP + timestamp + HMAC
→ validate schema
→ persist + dedupe
→ ACK 202
→ query event details
→ query current state
→ require supported schema_version
→ require event revision >= notification revision
→ require data_source == live
→ require test_mode == false
→ require freshness == fresh
→ apply consumer-owned deterministic policy
→ audit outcome
```

禁止：

- 只解析 Webhook body 后直接执行动作；
- 把 `replay`、`synthetic` 或 `test_mode=true` 当实时事件；
- 在 MCP 失败、stale、offline 时继续沿用旧 HR；
- 让 LLM 独自绕过确定性检查；
- 把 `lead_off`、`adc_clipping`、高 HR 或 SQI 解释成医疗诊断；
- 把 P0 事件直接映射为 `APPROACH`、前进或其他物理运动。

`CHECK_IN`、`WAIT` 等自然语言交互也不属于本仓库 Health MCP 的工具结果；若外部 Agent需要，应在其仓库另行定义。

## 9. 安全、鉴权与隐私

### 9.1 MCP

- P0 stdio 依赖本机进程边界，不在网络监听；
- P1 HTTP 只允许 loopback、Origin 校验和显式 client 启动；
- 远程 MCP 必须使用 HTTPS 和符合 MCP 授权规范的资源服务器设计；
- 不接受明文 LAN Bearer；
- 不记录 Authorization header；
- 工具只读，不能修改采集、设备、事件或机器人。

### 9.2 Webhook

- loopback 之外只允许 HTTPS；
- 独立 HMAC secret，按 7.3 固定为 32 bytes、配置为 64 位 lowercase hex；
- secret hex 从环境变量或 ignored `config/local.ps1` 注入，不提交仓库；
- 支持 key ID 轮换和撤销；
- body 不包含连续波形或生理指标；
- 日志默认只记录 notification/event ID、HTTP status、attempt、延迟和 trace ID。

建议环境变量：

```text
SMART_COLLAR_WEARER_ID
SMART_COLLAR_HEALTH_WEBHOOK_URL
SMART_COLLAR_HEALTH_WEBHOOK_KEY_ID
SMART_COLLAR_HEALTH_WEBHOOK_SECRET_HEX
SMART_COLLAR_HEALTH_DB_PATH
```

真实值不得放入任何 tracked 文件，不得出现在测试 fixture、日志或截图；`.env.example` 只能放变量名和 `<64-lowercase-hex>` 占位符。机器契约 golden 的 `webhook_secret_hex` 必须直接作为测试环境变量值解析，解析结果再用于 HMAC，不得绕过配置 parser 直接构造 bytes。

### 9.3 身体连接安全

本功能的开发和契约测试不要求人体 ECG：

- USB 调试只用于无人体电极的电子测试；
- 人体 ECG 必须独立电池供电并使用无线传输；
- 人体佩戴时不得连接桌面 USB、墙电、充电宝或接地台式仪器；
- 未提供真实 battery-powered wireless 日志时，不得声称人体路径已验证；
- MCP/Webhook 通过不等于医疗或电气安全通过。

## 10. 持久化、保留和删除

### 10.1 内存

- ECG/IMU 环形缓冲沿用当前约 30 秒；
- MCP 不新增连续波形持久化；
- 当前 `WearerState` 只保留最新 revision 和构建状态。

### 10.2 SQLite

建议数据库：ignored 本地路径 `data/health/health_state.db`。

必须表级分离：

- state revision；
- events；
- health webhook outbox；
- health webhook dead letter；
- MCP invocation audit。

默认保留：

| 数据 | 默认保留 |
|---|---:|
| resolved events | 7 天 |
| 成功 Webhook delivery metadata | 7 天 |
| dead letter | 30 天 |
| MCP audit（不含完整生理 data） | 30 天 |
| active events / pending outbox | 直到 resolved / 完成 |

自动清理必须在 transaction 中执行，不得删除 active event 或 pending outbox。

### 10.3 现有 raw session

`data/sessions/**/raw.bin` 是现有显式录制功能，不由 Health MCP 自动创建，也不受上述 7/30 天规则自动删除。UI 和文档必须明确它是单独的本地原始记录。

### 10.4 删除

P0 不暴露删除 MCP tool。实现必须提供本机管理员删除路径，要求：

- 应用停止采集或获得排他 DB lock；
- 明确 wearer、时间范围和受影响记录计数；
- 二次确认；
- 删除 event、outbox、dead letter、audit；
- 不自动删除 `data/sessions` raw recording；
- 写入不含生理内容的本地删除审计。

## 11. 可观测性

必须公开但不得泄漏敏感值的指标：

- state revision；
- source instance ID；
- state age/freshness；
- event open/resolve count；
- outbox pending/oldest age；
- delivery attempt/success/retry/dead-letter count；
- MCP call count/latency/error code；
- parser packets/loss/CRC；
- analysis last success/age/message；
- DB migration version。

每次 MCP call、event transition 和 Webhook delivery 必须有 `trace_id`。不得记录：

- raw/cleaned ECG 数组；
- 完整 HR 历史；
- secret/token/signature；
- Authorization header；
- 完整 Webhook body；
- 真实姓名或不必要的 wearer 个人信息。

## 12. 详细验收

### 12.1 Schema 与 wire

- [ ] 机器契约能按 UTF-8 解析；
- [ ] 所有内部 `$ref` 可解析；
- [ ] 所有对象禁止未声明字段；
- [ ] 所有 nullable 字段明确使用 union 或 `null`；
- [ ] `tools/list` 返回 4 个固定 P0 tool；
- [ ] 每个 tool 同时有 `inputSchema` 和 `outputSchema`；
- [ ] 成功 `structuredContent` 通过 output schema；
- [ ] 领域失败 `structuredContent` 通过 output schema；
- [ ] `content[0].text` JSON 与 `structuredContent` 深度相等；
- [ ] 成功 `isError=false`，领域失败 `isError=true`；
- [ ] unknown tool 和 malformed request 使用 JSON-RPC error。
- [ ] 成功、领域失败、unknown tool 和 malformed request 的完整 JSON-RPC golden 全部通过；
- [ ] `STATE_OFFLINE` 完整 wire 和各工具 `ToolMeta` golden 通过；
- [ ] Schema 拒绝 valid metric 的 `age_ms>2000`、`window_s!=10` 或空 provenance；
- [ ] Schema 拒绝嵌入 WearerState 时 `device.last_ecg_packet_age_ms=null`；

### 12.2 状态

- [ ] 500 Hz / 50 Hz 常量来自当前协议定义；
- [ ] 每次 reader start 生成新 `source_instance_id`；
- [ ] pending-reset window 缓冲所有 parser 接受的入站类型，包括 voice，并排除仅出站 ACK；
- [ ] ECG reset candidate 与 voice packet 交错、同 utterance 新 header 重传、voice-triggered reboot、确认/失败重放都有确定性测试；
- [ ] coordinator 位于 sequence/loss commit、ring append、voice durable/ACK 之前；buffered voice 不提前 ACK；
- [ ] rejected trigger 不污染 packets_ok/loss，confirmed/rejected replay 对每个 committed packet 只计数一次；
- [ ] raw recorder 在 decode/coordinator 前逐字节记录一次；坏 CRC、rejected trigger 仍保留，replay 不重复写；
- [ ] raw uint32 ECG index 跨 `0xffffffff→0` 后扩展 ordinal 仍单调，ring/analysis/state/golden 一致；
- [ ] serial/BLE runtime、ECG ring、analysis provenance 和 WearerState 共用一个 ordinal extender；raw last=23 时 ordinal last=4294967319；
- [ ] age 使用单调 receive clock；
- [ ] serial/BLE 以同一 `PacketReceipt` 原子记录 ECG、transport 和 status monotonic/UTC，replay 不重新取时；
- [ ] `last_ecg_packet_age_ms` 与 `last_transport_packet_age_ms` 分开计算，`WearerState.age_ms` 等于前者；
- [ ] 非 ECG 流量持续而 ECG 停止时，2 秒后 state stale、10 秒后 offline，HR/RR 不再有效；
- [ ] transport fresh、ECG 超过 10 秒时 `device.status=degraded` 且 current-state 返回 `STATE_OFFLINE`，不返回 `DEVICE_OFFLINE`；
- [ ] ECG 持续但 analysis worker 停止时，最后分析证据超过 2 秒后 HR/RR 失效；
- [ ] analysis source mismatch、sample index 超前/淘汰和缺 receipt 都返回 `stale_data`；
- [ ] current/device/event/recent success 及所有 failure 的 ToolMeta 按 6.4 映射，mixed recent source 为 null；
- [ ] 重启后旧状态不恢复为 fresh；
- [ ] HR/RR/SQI 不可用时分别给出原因；
- [ ] lead-off/clipping/stale 门控使相应生理指标无效；
- [ ] P0 motion 固定为 `not_implemented`，不伪造分类；
- [ ] replay/synthetic/test 数据永远不标成生产 live。

### 12.3 事件

- [ ] 4 个 P0 event 的打开、维持、解除和迟滞均有边界测试；
- [ ] 同类型 active event 不重复创建；
- [ ] 只有 status/voice、从未收到 ECG、lead-off=true 时不创建 event/outbox；首个 ECG 后用首个 state revision 立即重评；
- [ ] resolved 后再次触发使用新 event ID；
- [ ] event 与 outbox 同 transaction；
- [ ] 重启恢复 active event 和 pending outbox；
- [ ] voice/IMU/status 流量不得解除或阻止 `input_stale` / `input_offline`；
- [ ] stale→offline 在一个 transaction 内按“resolve stale / sequence N，再 open offline / sequence N+1”执行，并共享同一 state revision。

### 12.4 Webhook

- [ ] endpoint 精确为 `/v1/health-events`；
- [ ] 非 loopback `http://` 配置失败；
- [ ] 原始 body HMAC golden vector 通过；
- [ ] secret parser 只接受 64 位 lowercase hex；缺失、uppercase、`0x`、空白、奇数/非 64 长度、非 hex 均在启动前失败且不发网络请求；
- [ ] 非 POST 返回 `405 method_not_allowed` 和 `Allow: POST`，错误 path 返回 `404 not_found`；
- [ ] 错误 Content-Type 返回 `415 unsupported_media_type`，缺失/非法/零 Content-Length 和 chunked 返回 `400 invalid_request`；
- [ ] 声明或实际 body 超过 65536 bytes 返回 `413 body_too_large`；
- [ ] 错 key、错签名返回 `401 invalid_signature`，过期时间戳返回 `401 timestamp_out_of_range`；
- [ ] 非 UTF-8、非法 JSON、Schema 失败返回 `400 invalid_request`；
- [ ] 新通知持久化后返回 202 accepted；
- [ ] Header notification ID 与 body 不一致时返回 400；
- [ ] timestamp 前导零、符号、小数、越界和超过 ±300 秒全部拒绝；
- [ ] `Accept` 缺失或为其他值时不影响同一合法请求的验签、持久化和 202 ACK；
- [ ] 相同 ID/相同 raw-body SHA-256 返回 202 duplicate；
- [ ] 相同 ID/不同 raw-body SHA-256 返回 409；
- [ ] 同 ID 并发请求由唯一约束保证一个 accepted，其余按 digest 为 duplicate 或 409，queue 恰好写一次；
- [ ] 持久化/去重失败返回 `503 internal_error`，且 receiver 自身不产生 `403`；
- [ ] 所有失败 body 均通过 `WebhookErrorAck`，且不泄漏 secret、签名、body 或内部异常；
- [ ] retryable/terminal 状态分类与本文一致；
- [ ] 301/302/303/307/308 均不跟随、不访问 `Location`、不泄漏签名/body，并作为 terminal；
- [ ] timeout、退避、jitter、Retry-After 有确定性测试；
- [ ] 应用重启后继续发送相同 notification ID/body；
- [ ] 24 小时后进入 dead letter；
- [ ] 普通 instruction/reply 队列堵塞不影响 health queue。

### 12.5 安全与仓库

- [ ] MCP P0 仅 stdio；
- [ ] loopback HTTP 不默认开启；
- [ ] secret 不进入 Git、日志和错误体；
- [ ] raw ECG 不进入 MCP/Webhook；
- [ ] 不修改固件协议或采样；
- [ ] 不增加机器人动作工具；
- [ ] 不作医疗诊断声明；
- [ ] `.\tools\project.ps1 pc-test` 通过；
- [ ] `git diff --check` 通过。

### 12.6 人工但非人体 smoke test

允许使用 synthetic fixture 或无人体电极的电子信号：

1. 启动 PC state builder；
2. 注入 golden packet/vector；
3. 观察 state revision；
4. 调用 4 个 MCP tools；
5. 触发/解除 4 个事件；
6. 用本地 mock receiver 验签、去重和 ACK；
7. 断开输入 2 秒和 10 秒验证 stale/offline；
8. 确认未打开 serial monitor、未 flash、未连接人体。

## 13. 实施顺序与完成定义

建议按以下 PR/commit 拆分：

1. `test(pc): add health contract fixtures`

   加入机器契约加载、golden validation、无运行时代码。
2. `feat(pc): add health state builder`

   模型、quality、freshness、source instance 和状态快照。
3. `feat(pc): add health event store`

   SQLite migration、event lifecycle、outbox transaction。
4. `feat(pc): add read-only health mcp server`

   4 个 tools、stdio、wire/error contract。
5. `feat(pc): add signed health webhook dispatcher`

   HMAC、HTTPS policy、retry、dead letter。
6. `docs(pc): document health mcp operations`

   配置、启动、监控、密钥轮换、删除和故障排查。

一个阶段只有在相应测试通过、文档同步、未提交 secret/raw data 后才算完成。P0 总完成还要求：

- 所有 12 节验收项通过；
- 5 分钟 synthetic/bench soak，无 state builder crash、DB lock 泄漏或 outbox 丢失；
- 进程 kill/restart 后 active event 与 pending outbox 恢复；
- MCP client contract test 和 mock receiver contract test 各自独立通过。

## 14. v0.1 驳回点关闭矩阵

| 驳回点 | v0.2 处理 |
|---|---|
| 缺完整 Webhook | 第 7 节冻结 endpoint、headers、HMAC、body、ACK、timeout、retry、重复、乱序 |
| 包络不知放 MCP 哪里 | 第 6.4–6.7 节固定 `structuredContent`、TextContent、`isError`、JSON-RPC error |
| 没有正式 JSON Schema | 机器契约提供 required、nullable、范围、枚举、`additionalProperties` 和 golden |
| LAN 明文 Token/健康数据 | MCP P0 改 stdio；HTTP 仅 loopback；远程要求 HTTPS/MCP auth；Webhook 非 loopback 只 HTTPS |
| 假设已有安全状态机 | 明确本仓库没有，P0 不承诺消费策略或机器人动作 |
| `APPROACH` 不可安全实现 | 从 P0 和工具中完全删除 |
| 健康事件进入普通 Agent FIFO | health endpoint、queue、线程和去重命名空间全部独立 |
| `high_hr_low_motion` 无阈值 | 从 `0.2.0` 事件枚举删除，等 motion/baseline 实现后再升级 |
| `quality_level >= fair` 错误 | 固定 `quality_rank` 和数值映射 |
| `active_events` 类型不明 | 固定为 `EventRef[]` |
| recent events 缺 cursor 输入 | 工具输入和分页语义补齐 |
| `age_ms` 基准不明 | 第 2.4 节固定 PC monotonic receive clock |
| 设备时间无 boot ID | 使用并明确 PC-generated `source_instance_id` |
| 多指标共用 unavailable reason | 每个 metric 独立 `valid` + `unavailable_reason` |
| manual mock 无法测主链 | 使用 `data_source=synthetic` + `test_mode=true`，完整跑契约但禁止生产动作 |
| 动作命名不一致 | Health MCP 不再返回机器人动作 |
| Resource Template 不完整 | P0 不声明 resources capability，避免半成品资源契约 |
| 限流范围不明 | 第 6.9 节固定 server-enforced scope 与额度 |
| 缺保留/删除/访问控制/审计 | 第 4.2、9、10、11 节补齐 |
| 与当前仓库芯片/采样不符 | 按仓库写成 ESP32-C3、ECG 500 Hz、IMU 50 Hz；不再写经典 ESP32、ESP32-S3 或 ECG 250 Hz |
| 当前 HRV/motion 并不存在 | 从 P0 tools/events 移除并明确 `not_implemented` |

## 15. 明确留待后续版本

以下内容不得在实现 `0.2.0` 时顺手加入：

- HRV / RMSSD；
- 运动级别、头部姿态或跌倒判断；
- 个体 baseline；
- `high_hr_low_motion`；
- 远程 MCP；
- OAuth authorization server；
- MCP resources；
- 机器人、DimOS、定位、接近、避障；
- 医疗报警、急救联络或诊断。

每项都需要独立 ExecPlan、Schema 版本、验证证据和安全审查。

## 附录 A：实现者交接清单

开始编码前，开发者只需确认：

1. 目标分支基于当前 `main`；
2. `wearer_id` 配置值；
3. 使用 stdio 还是额外实现 loopback HTTP（默认仅 stdio）；
4. 本地 health DB 路径；
5. mock Webhook URL、key ID 和 secret；
6. 实现开始时将兼容 MCP `2025-11-25` 的官方 Python SDK 精确版本写入依赖锁定；不需要消费方临时选择。

其余 P0 行为不得在联调时临时口头决定，应以本文和机器契约为准。

## 附录 B：官方 MCP 依据

- [Tools：`inputSchema`、`outputSchema`、`structuredContent`、`isError`](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
- [Transports：stdio、Streamable HTTP、Origin 与 loopback](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [Authorization：HTTP 授权、protected resource metadata 与 audience](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
