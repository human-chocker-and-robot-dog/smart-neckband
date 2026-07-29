# Quote/0 BLE 健康显示固件交接计划

## Goal

在 InkCanvas `2.1.0-lp` / `baseline-2.1.0-lp` 基础上，把 MindReset Quote/0 改造成可由 Windows 上位机通过加密 BLE GATT 驱动的独立电子墨水健康显示终端，同时保留现有 USB Web Serial、5,624 字节 framebuffer、CRC32、UC8251D 安全 LUT、全刷/局刷、RTC 上一帧和可恢复升级能力。

固件只接收和显示 PC 已经渲染好的 296×152、1-bit 完整帧，不在 Quote/0 上计算 ECG、R 峰、HR、HRV 或 SQI。显示布局由上位机负责，目标画面从上到下是 `★ + BPM`、`HRV`、导联状态、`ECG · 最近 8 秒 · SQI` 和较大的 ECG 波形区域。

## Current state

- 源码仓库：`C:\Users\XWen1024\Documents\Codex\2026-07-26\xia\work\InkCanvas-Rewrite`。
- 不可变基线：`baseline-2.1.0-lp` / `6a721b0c278a3f6c8bef94c4aab22dfa021f4e4e`；计划在 `refactor` 或从该基线创建的专用功能分支上实施。
- MCU 是 ESP32-C3 QFN32 revision v0.4，4 MB Flash；面板 UC8251D，296×152 横向、1-bit、5,624 字节，四线 SPI 15 MHz。
- `firmware/sdkconfig.defaults` 当前配置为 `CONFIG_BT_CONTROLLER_ONLY=y`、`CONFIG_BT_NIMBLE_ENABLED=n`，只有控制器启停，没有 BLE host 或 GATT 服务。
- `firmware/main/radios.c` 只初始化/启停 BLE controller。
- `firmware/main/protocol.c` 同时承担 USB Serial/JTAG I/O、ASCII/JSON 协议、二进制 FRAME/SCENE 状态、刷新策略、离线场景调度和深睡检查；`protocol_run()` 是永久阻塞循环。
- `firmware/main/epd_uc8251.c` 已验证 UC8251D 初始化、376 字节保守 LUT、`GET_STATUS (0x71)` 后读取 BUSY、全刷、局刷窗口、RTC 前帧和刷新后深睡。
- 当前全刷与局刷都使用同一保守 LUT，真机协议耗时约 4.1 秒；局刷主要减少无关区域闪烁，不是快速动画模式。
- 当前维护性全刷默认 30 分钟，最小 3 分钟；`partialCount` 只用于诊断，不参与隐藏阈值。
- 当前启用 BLE 会使 `power_manager_should_sleep()` 永远不进入自动深睡，这符合控制器开关语义，但没有为长期在线同步设计 modem sleep、连接状态或低电量降级。
- 当前 Flash 分区和 5,624 字节 RTC 上一帧已经足够，本计划不需要改分区表。

## Scope

Included:

- 从 controller-only 迁移到 ESP-IDF NimBLE host 和专用 BLE GATT Peripheral 服务。
- 把 USB 传输、BLE 传输、帧事务和屏幕刷新任务拆分成清晰模块。
- 实现 BLE Display Protocol v1：设备信息、BEGIN、顺序分块、COMMIT、取消、状态通知和错误码。
- 使用加密、绑定和专用广播名/服务 UUID。
- 在屏幕刷新期间继续处理 BLE 事件，并允许缓存一张最新待显示帧。
- 新增适合长期在线同步的运行/电源状态机。
- 把维护性全刷改成“最大时间＋最大有效局刷次数”双阈值并提供可配置默认值。
- 保留原 USB v5 和网页编辑器工作流的兼容性。
- NVS schema 迁移、协议文档、构建验证、Windows BLE 联调和真机残影/功耗测试。

Excluded:

- 在 Quote/0 固件里实现 NeuroKit2、ECG 滤波、R 峰、HR 或 HRV 算法。
- 修改项圈固件、项圈 BLE 服务或项圈原始数据协议。
- 更换 GPIO、面板、SPI 时钟、UC8251D 初始化序列或未经验证的快速局刷 LUT。
- 修改 Flash 分区、擦除原始备份、变更 eFuse 或在未授权情况下刷机。
- 医疗诊断或医疗设备声明。
- 为了省电而让同步模式进入无法被 BLE 唤醒的深睡，然后宣称仍能即时连接。

## Design decisions

### 1. 保持 framebuffer 为传输边界

- BLE 继续使用 296×152 横向 1-bit 完整帧，长度固定 5,624 字节，像素格式和 USB v5 完全一致。
- CRC 继续使用 CRC-32/ISO-HDLC。
- Quote/0 不解析 HR、HRV、SQI 或 ECG 数据结构，不承担字体、布局或健康指标语义。
- USB 与 BLE 共用同一个帧提交和显示队列核心，避免两套刷新策略产生不同结果。

### 2. 模块拆分建议

建议在 `firmware/main/` 新增或重构为：

| 模块 | 职责 |
| --- | --- |
| `display_frame_service.*` | transport-independent BEGIN/CHUNK/COMMIT、offset、CRC、超时、owner 和双缓冲 |
| `display_worker.*` | 低优先级屏幕任务、最新待显示帧、刷新结果和状态发布 |
| `ble_display_service.*` | NimBLE GAP/GATT、安全、广告、characteristic 回调和通知 |
| `usb_protocol.*` | 现有 USB v5 文本/JSON 和 FRAME/SCENE 适配 |
| `display_status.*` | 线程安全的连接、接收、队列、刷新、电量和错误快照 |
| `power_manager.*` | 保留旧离线模式并增加 sync 模式状态机 |

不要求机械地使用这些文件名，但必须实现同样的边界。`epd_display_landscape_update()` 只能由显示任务调用，不能在 NimBLE GATT callback 或 USB 接收 callback 中调用。

### 3. 并发和缓冲

- 至少保留两个 5,624 字节缓冲：一个供显示任务稳定读取，一个供当前 BLE/USB 事务接收。
- 可再保留一个 latest-pending 槽位；总 RAM 增量在 ESP32-C3 上可接受，但实现后必须检查 heap 水位。
- GATT callback 只校验短头、顺序 offset、长度并复制数据，绝不等待约 4.1 秒屏幕刷新。
- COMMIT 在长度和 CRC 均通过后原子地提交 frame ID；显示任务完成当前刷新后只取最新待显示帧。
- 不补刷被更新帧覆盖的中间画面，记录 `pending_replaced_count`。
- USB 与 BLE 同时发帧时使用 transport owner：一个事务在途时另一个收到明确 `BUSY`，不得交叉写同一缓冲。
- 二进制事务 10 秒无有效数据时取消并释放 owner，保持与 USB v5 类似的恢复语义。

### 4. BLE Display Protocol v1

Quote/0 仓库提交线协议规范原件 `docs/PROTOCOL_BLE_DISPLAY_V1.md`，固定 UUID、协议版本、字节序、错误码和机器可读黄金向量。`smart-neckband` 仓库保存带本规范源 commit 的兼容副本；C 与 Python 测试必须读取相同黄金向量，避免两个仓库各自演化。建议提供一个 128-bit primary service 和四个 characteristic：

1. `DEVICE_INFO`：read。
2. `CONTROL`：write with response。
3. `FRAME_DATA`：write with response。
4. `STATUS`：read + notify。

第一版优先可靠性，不使用无响应高速写。5,624 字节相对约 4.1 秒屏幕刷新很小，180 字节左右的数据块已经足够。

`BEGIN_FRAME` 至少包含：

- protocol version；
- frame ID；
- length，必须等于 5,624；
- expected CRC32；
- rotation，90 或 270；
- refresh request：auto 或 force full；
- source ECG sample index；
- source device monotonic timestamp us。

`FRAME_DATA` 包含 frame ID、offset、payload length 和 payload。固件只接受当前 frame ID 的下一个顺序 offset；重复块可以幂等确认，跳跃、越界或不同 frame ID 必须报错。

`COMMIT_FRAME` 触发长度/CRC 检查并进入显示队列。`STATUS` 至少报告：

- state：ready/receiving/queued/refreshing/done/error；
- active/last frame ID；
- received bytes；
- actual refresh mode：none/full/partial；
- changed region；
- refresh ms；
- partial refresh count；
- milliseconds since last full refresh；
- battery mV/percent；
- last error；
- pending replacement、CRC error、timeout 和 display failure counters。

所有整数使用 little-endian。建立 C 编解码黄金向量，并由 PC Python 测试读取同一向量。

### 5. BLE 广播与安全

- 广播名使用 `InkCanvas-Quote0-XXXX`，XXXX 来自 MAC 后两字节；不要复用 `CollarC3-` 或 Nordic UART Service UUID。
- 广播中包含专用 BLE Display service UUID，Windows 上位机优先按 UUID 过滤。
- 所有写特征要求加密；绑定信息保存于 NVS。
- 第一里程碑可采用 LE Secure Connections Just Works 以完成稳定联调，但发布候选应评估 `DisplayOnly` passkey：在电子纸上显示首次配对码，由 Windows 输入，从而为健康数据提供 MITM 身份校验。
- 未加密连接不得接受 BEGIN、FRAME_DATA 或 COMMIT。
- 断开后清除未完成事务，不清除已显示画面和可信 RTC 前帧。

### 6. 刷新策略与残影控制

保留以下既有安全策略：

- 首帧、RTC 前帧无效、方向变化、用户禁用局刷时全刷。
- 变化区域达到屏幕面积 75% 时全刷。
- 无像素变化且维护阈值未到时返回 `none`。
- 继续使用已验证完整 LUT，不在本功能中研究快速 LUT。

把维护性全刷从纯时间改为双阈值：

- 默认 `full_refresh_ms = 300000`，即约 5 分钟；保留最小 180000 ms，允许配置到 30 分钟以上供实验。
- 默认 `max_partial_refreshes = 20`，只统计实际执行成功的局刷；全刷后清零。
- 时间或次数任一先到，下一次提交即执行全刷，即使 framebuffer 与当前显示相同也允许进行维护性全刷。
- 刷新失败不得更新上一帧、时间或计数基线。
- 时间和计数保存在 RTC/NVS 的选择要有断电语义说明；至少深睡唤醒后不能意外无限延后维护性全刷。

默认值不是最终面板寿命结论。真机必须用 10–15 秒画面更新建立残影矩阵，比较 5、10、20、30 次局刷以及 3、5、10 分钟时间阈值，再决定发布默认值。用户明确认为 20–30 分钟可能过长，因此不得未经测试继续沿用 30 分钟作为同步模式默认值。

### 7. 电源状态机

保留旧网页/离线使用方式，并新增明确的同步模式，不要把两种相反目标塞进一个隐式布尔组合。

建议 NVS schema v3 增加：

- `operating_mode = legacy | sync`；
- `sync_refresh_min_ms`，默认 15000，最小 10000；
- `max_partial_refreshes`，默认 20；
- `sync_disconnect_policy`；
- 低电量阈值和回差在 ADC 标定完成前只保留配置/遥测，不宣称保护已验证。

状态语义：

- `LEGACY`：完全保留当前 USB/离线场景、定时唤醒和极致省电行为。
- `SYNC_ADVERTISING`：BLE host 运行并持续可连接；允许 ESP-IDF modem sleep/动态频率，但不得进入关闭无线电的 deep sleep。
- `SYNC_CONNECTED`：保持 GATT，接收帧；屏幕控制器在两次刷新之间继续 `0x07 A5` 深睡并释放 GPIO20。
- `SYNC_REFRESHING`：屏幕任务运行，BLE host 和 GATT callback 保持可调度。
- `SYNC_LOW_BATTERY`：在电池 ADC 完成实测标定后，降低刷新频率、通知 PC，并可显示一次低电量画面；最终深睡策略必须明确 PC 无法通过 BLE 唤醒。
- `MANUAL_SLEEP`：保留 USB/VBUS 唤醒；进入前通知 PC，断开 BLE。

即时同步模式接受比旧固件更积极的电量消耗。不得为了报告低功耗而在 PC 仍期望即时连接时进入 deep sleep。应开启 ESP-IDF power management、自动 light/modem sleep，并实测 advertising、connected idle、传输、局刷、全刷和屏幕深睡电流。

### 8. USB/Web 向后兼容

- USB v5 的 HELLO、CONFIG、FRAME、SCENE、SENSORS、SLEEP 和 CLEAR 继续工作。
- `protocol_run()` 应被重构为 USB task 或轮询适配器，但外部命令和 JSON 字段不得无故改变。
- HELLO capabilities 增加 BLE display protocol、sync mode 和真实 `maxPartialRefreshes`，协议版本变更必须记录。
- NVS schema v2→v3 迁移保留现有 Wi-Fi、BLE、局刷、离线场景和休眠配置；迁移失败回退安全默认值而不是擦除整个 NVS。
- 不改分区表。日常升级继续只写 app 分区并保留 NVS 与 scene。

## Work breakdown

1. 从 `baseline-2.1.0-lp` 建立专用功能分支，记录当前应用镜像、分区表和原始备份哈希，不改 LUT/GPIO/分区。
2. 定义 BLE Display Protocol v1、UUID、C 结构、状态机、错误码和 C/Python 共用黄金向量。
3. 把现有 FRAME 接收、CRC、刷新策略和结果生成从 USB I/O 中抽成 transport-independent service，保持 USB v5 行为。
4. 新增显示 worker、双缓冲、transport owner 和 latest-pending 队列；证明屏幕刷新不在 GATT callback 中执行。
5. 把 sdkconfig 从 controller-only 改成 NimBLE host，增加 BLE GATT service、广告、加密、绑定、通知和断连清理。
6. 实现 BEGIN/CHUNK/COMMIT/CANCEL/STATUS，覆盖顺序 offset、重复块、越界、CRC、超时、忙状态和 pending replacement。
7. 实现 `legacy`/`sync` 电源状态机、modem sleep、连接/刷新状态和屏幕刷新间休眠。
8. 实现 5 分钟或 20 次局刷双阈值，更新 CONFIG/HELLO/NVS schema 和迁移逻辑。
9. 更新 `docs/PROTOCOL.md`、`docs/HARDWARE.md`、`docs/ARCHITECTURE.md`、固件 README、版本号和发布说明。
10. 完成构建、USB 回归、Windows Bleak 联调、残影矩阵、稳定性和功耗测试，再生成发布镜像和哈希。

## Validation

固件与网页回归：

```powershell
idf.py set-target esp32c3
idf.py build
cd web
& "C:\Program Files\nodejs\npm.cmd" run typecheck
& "C:\Program Files\nodejs\npm.cmd" test
& "C:\Program Files\nodejs\npm.cmd" run build
```

如果该机器仍需要交接文档记录的显式 ESP-IDF/Ninja/GCC/Python 环境，使用现有已验证工具路径，不另造不受控工具链。构建后记录：

- app、IRAM、DRAM、Flash 使用量；
- NimBLE 启用后的最小 free heap；
- 两个或三个 framebuffer 缓冲后的峰值 heap；
- 分区表未变化；
- app-only 升级仍保留 NVS 和 scene。

协议/软件测试至少覆盖：

- C 与 Python 对相同 BEGIN、CHUNK、STATUS 和 CRC 黄金向量解释一致。
- 错误长度、错误 CRC、错误 offset、重复块、乱序块、超时、取消和断连。
- USB 与 BLE 同时开始事务时的 BUSY/owner 行为。
- 刷新期间继续接收 BLE 状态请求和一张最新 pending 帧。
- no-change、局刷、75% 区域全刷、强制全刷、5 分钟阈值和 20 次阈值。
- 刷新失败不污染可信上一帧或维护计数。
- NVS v2→v3 迁移和 legacy 模式回归。

真机验证需要明确授权刷机后执行，且必须先保存可恢复镜像：

1. USB v5 HELLO、CONFIG、FRAME、SCENE、CLEAR 和深睡回归。
2. Windows 扫描 `InkCanvas-Quote0-*`、首次配对、绑定复用、加密写权限和重连。
3. 发送已知棋盘、固定布局和全白/全黑/小窗口测试帧，核对 CRC、方向、变化区域和实际画面。
4. 在约 4.1 秒刷新期间发送下一帧，确认最新 pending 语义和 GATT 不阻塞。
5. 每 10–15 秒更新至少 30 分钟；发布候选应完成更长 soak，记录断连、复位、CRC、超时、显示失败和 heap 水位。
6. 残影矩阵比较 5/10/20/30 次局刷和 3/5/10 分钟全刷间隔，并记录温度和电池电压。
7. 使用功耗仪测量 legacy deep sleep、sync advertising、sync connected idle、帧传输、局刷、全刷和 EPD sleep。

这些测试只验证显示终端，不构成人体 ECG 或医疗验证。Quote/0 与项圈之间没有电气连接；人体采集安全约束仍由项圈项目单独执行。

## Risks and rollback

- NimBLE host、GATT 数据和多缓冲会增加 RAM。必须在合入前记录 heap 水位，若不足先减少并发场景缓冲，不得破坏 RTC 前帧校验。
- `protocol.c` 当前承担过多职责，重构可能破坏 USB v5。每一步保持 USB 回归，先抽核心再加 BLE。
- 在 GATT callback 中直接刷屏会阻塞 host 任务并导致断连，这是禁止实现。
- sync 模式无法兼得 deep sleep 和 PC 即时唤醒。若功耗超标，只能通过降低广播/连接参数、modem sleep、降低刷新频率或明确的 session 结束策略优化，不能虚构 BLE 深睡唤醒。
- 过短维护性全刷会增加闪屏和能耗，过长会增加残影。5 分钟/20 次是待验证初始值，最终以真机矩阵为准。
- 配对与 GATT 缓存可能在 Windows 上留下旧服务。服务版本或 UUID 改变时必须提供清除绑定/缓存的操作说明。
- 回滚使用 app-only 写回 `baseline-2.1.0-lp` 兼容镜像，保留 NVS/scene；必要时使用已保存整片备份恢复。禁止 force push、历史改写或未经确认整片擦除。

## Progress

- [ ] 建立固件功能分支和恢复点。
- [ ] 固定 BLE Display Protocol v1 和黄金向量。
- [ ] 抽离 transport-independent frame service。
- [ ] 实现显示 worker、双缓冲和 latest-pending。
- [ ] 启用 NimBLE 并实现安全 GATT 服务。
- [ ] 实现 sync 电源状态机。
- [ ] 实现 5 分钟/20 次维护全刷初始策略。
- [ ] 完成 USB/Web 回归和固件构建。
- [ ] 完成 Windows BLE 真机联调。
- [ ] 完成残影和功耗矩阵。
- [ ] 更新版本、文档、镜像和哈希。

## Discoveries

- 当前固件不是“缺一个 GATT characteristic”，而是明确的 controller-only 配置；必须启用 NimBLE host 并重新设计传输/任务边界。
- 当前局刷仍写完整新旧 RAM 并使用完整 LUT，因此 BLE 帧传输速度不是系统瓶颈，屏幕约 4.1 秒刷新才是主要节奏限制。
- 现有 RTC 上一帧、差异窗口、无变化跳过和 app-only 升级是非常适合复用的基础，不应为 BLE 改造重写面板驱动。
- 原电源策略面向短暂 USB/Wi-Fi 配置后深睡；即时 BLE 同步必须采用更积极但诚实可测的在线电源模式。

## Result

尚未实施。本计划是交给 Quote/0 固件开发者的执行文档。完成后应在此记录实际提交、固件版本、构建尺寸、USB 回归、BLE soak、残影矩阵、功耗测量、发布镜像哈希和仍未验证的硬件事实。
