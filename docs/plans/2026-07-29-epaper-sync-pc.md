# 电子墨水屏同步上位机集成计划

## Goal

在现有 `smart-neckband` Windows Qt 主 GUI 中新增唯一入口的“墨水屏同步”页面，使上位机能够同时维护项圈 ESP32-C3 与 MindReset Quote/0 两条独立的加密 BLE GATT 连接：持续接收项圈 500 Hz 原始 ECG，在 PC 端完成 NeuroKit2 分析和显示帧渲染，再把 296×152、1-bit 的低频健康摘要画面发送到 Quote/0。

用户可在同一个 GUI 中扫描、连接、预览、启停和诊断电子墨水屏同步，不需要启动第二个程序。电子纸显示内容从上到下固定为：

1. 左上角星形心跳图标 `★`，右侧显示当前 `BPM`，屏幕上不写 `HR`。
2. `HRV <value> ms`；底层第一版使用 30 秒有效 NN/RR 窗口的 RMSSD，但屏幕只显示用户可理解的 `HRV`。
3. `导联正常`、`导联脱落`、`信号无效`或`等待数据`。
4. `ECG · 最近 8 秒 · SQI <value>`；本计划把用户标注中的 “SQRT” 按项目现有指标解释为 `SQI`，不新增名为 SQRT 的指标。
5. 尽可能大的 ECG 波形区域，不显示更新时间。

## Current state

- `pc_app/src/smart_neckband/gui.py` 已经是统一 GUI，使用 `QTabWidget` 集成实时、诊断、历史、对比、麦克风、Sleep ECG、Health/MCP 和 Webhook 页面。
- 项圈 BLE 由 `pc_app/src/smart_neckband/ble_io.py` 中的 `BlePacketReader` 独占后台线程和 asyncio 事件循环；连接使用 Bleak、自动配对、加密通知和有界控制写队列。
- `pc_app/src/smart_neckband/analysis.py` 使用 10 秒窗口、9.5 秒重叠，约每 0.5 秒产生清洗 ECG、R 峰、HR、RR 和 SQI。
- `pc_app/src/smart_neckband/health_rules.py` 和 Health 数据链已经存在 RMSSD 计算逻辑，但当前实时 `EcgAnalysisResult` 不直接暴露 HRV。
- 当前上位机没有 Quote/0 扫描、连接、帧渲染、帧发送或屏幕状态页面。
- Quote/0 面板是 UC8251D，横向画布 296×152，1-bit、MSB-first，单帧 5,624 字节。当前保守 LUT 下全刷和局刷真机都约 4.1 秒。
- Quote/0 的 BLE GATT 应用服务尚未实现；对应工作由独立固件计划负责。
- 项圈原始 ECG 主链必须保持 500 Hz、原始 ADC counts、原始包记录和既有 CRC/状态语义；电子纸渲染只能读取独立预览副本。

## Scope

Included:

- 在主 Qt GUI 中新增名为“墨水屏同步”的独立页签。
- 新增与项圈 `BlePacketReader` 解耦、但由 GUI 生命周期统一管理的 Quote/0 BLE 客户端。
- 扫描、配对、连接、断开、自动重连、帧事务、状态通知和错误诊断。
- 从现有 ECG 分析结果构建 HR、HRV、导联和 SQI 的显示状态。
- 把最近 8 秒清洗 ECG 转成适合 296×152 黑白屏的 min/max 包络波形。
- 生成 5,624 字节完整帧、计算 CRC32、节流、去重和发送最新帧。
- GUI 内预览电子纸实际位图，并展示设备电量、连接状态、刷新模式、刷新耗时、局刷计数和最后错误。
- 自动化测试、协议文档、用户操作文档和两设备台架验收。

Excluded:

- Quote/0 固件实现、刷写和面板驱动修改；这些由配套固件计划负责。
- 修改项圈 ESP32-C3 固件、引脚、采样率或 V0 原始 ECG 协议。
- 把 HRV、过滤波形或 BPM 写回或替换项圈原始 ECG 主链。
- 医疗诊断、异常心律判定或医疗级同步声明。
- 为电子墨水屏追求连续滚动 ECG；当前目标是低频快照。
- 人体连接验证；除非用户以后明确授权并提供独立电池无线测试条件。

## Design decisions

### 1. 单 GUI、双 BLE 客户端

- 主窗口仍是唯一用户入口。
- 新增 `EpaperSyncPanel`，由 `MainWindow` 创建、加入 `QTabWidget`，并在主窗口关闭时停止客户端和工作线程。
- 新增 `EpaperDisplayClient`，独占自己的后台线程和 asyncio 事件循环，不复用项圈的 NUS UUID，也不在项圈 notification 回调中执行任何屏幕操作。
- Windows 同时保持两个 Peripheral 连接：项圈连接只负责采集，Quote/0 连接只负责显示。
- 如果真机发现多个独立 WinRT 事件循环不稳定，再把两个连接迁移到共享 BLE runtime；第一版先保持与现有 `BlePacketReader` 一致的可测试线程边界。

### 2. Qt 页面内容

“墨水屏同步”页至少包含：

- 设备区域：扫描按钮、Quote/0 设备列表、连接/断开、自动重连、绑定状态。
- 同步区域：启用自动同步、刷新周期、手动“立即发送”、手动“下一帧强制全刷”。
- 预览区域：296×152 等比例放大的 1-bit 位图，必须显示与发送字节一致的结果。
- 指标区域：HR、HRV、SQI、导联状态、源样本索引、分析数据年龄。
- 设备状态：电池电压/百分比、BLE RSSI（若后端可用）、最后帧 ID、CRC、设备刷新模式、区域、刷新耗时和局刷累计。
- 诊断日志：默认折叠，沿用主 GUI 当前可折叠诊断区域的交互习惯。

页签集成不意味着后端耦合：GUI 只通过线程安全的状态快照、命令队列和 GUI callback 队列与客户端交互。

### 3. 固定屏幕布局

第一版画布使用 296×152 横向坐标，白底黑字，建议区域如下：

```text
y=0
┌─────────────────────────────────────┐
│ ★ 72 BPM                            │ 0..25
│ HRV 38 ms                           │ 26..44
│ 导联正常                            │ 45..60
│ ECG · 最近 8 秒 · SQI 91%           │ 61..77
│                                     │
│        ECG min/max 包络波形          │ 78..151
│                                     │
└─────────────────────────────────────┘
y=151
```

- 星形图标使用内置单色矢量/像素路径，不依赖系统字体是否包含特殊字符。
- BPM 是主视觉；数值无效时显示 `★ -- BPM`。
- HRV 的技术来源是 RMSSD，但屏幕不显示 `RMSSD`、`RMS`或`SSD`拆分文字。
- 导联脱落、严重 clipping、过期分析或 SQI 无效时，HRV 必须显示 `--`，不能沿用旧值伪装成当前值。
- 不显示更新时间；数据新鲜度只在 GUI 诊断区显示。
- ECG 标题保留“最近 8 秒”和 SQI，波形获得剩余最大高度。

### 4. ECG 波形渲染

- 波形来源是 `EcgAnalysisResult.cleaned` 的最近 8 秒独立副本；原始数据缓冲、记录文件和传输包不得被修改。
- 从约 4,000 个 500 Hz 样本映射到波形区域的可用列，每列计算 min/max 并绘制垂直包络，避免普通抽样漏掉窄 R 峰。
- Y 轴采用稳健范围，例如有限样本的 2%–98% 分位范围加小幅边距；无有限数据时显示空白基线和等待状态。
- 绘制函数必须是纯函数：输入显示状态和尺寸，输出 1-bit framebuffer 及可选预览图，不访问 Qt 控件或 BLE。
- 打包严格复用 Quote/0 约定：`frame[y * 37 + floor(x / 8)]`，`0x80 >> (x % 8)`，`1=白`、`0=黑`。

### 5. HRV 数据所有权

- 新增有界的 30 秒 NN/RR 内存窗口，由分析结果中的 RR observations 更新。
- 复用项目现有 `rmssd_ms` 纯函数和质量门限，不从 SQLite 反查，也不要求配置 Health wearer ID。
- 显示状态保留 `hrv_method="rmssd"`、窗口长度、有效 NN 数和有效性原因，屏幕只渲染 `HRV`。
- 第一版不显示 SDNN；若未来增加，必须显式标明窗口和有效性，不能混用 HRV 定义。

### 6. 刷新调度与最新帧语义

- PC 内部 HR/SQI 可每 0.5–1 秒更新，但默认电子纸发送周期为 15 秒，可配置范围为 10–60 秒。
- HRV 以 30 秒窗口更新；新 HRV 不要求立即打断正在进行的电子纸刷新。
- 以下事件可请求优先帧：导联状态变化、SQI 有效性变化、严重 clipping、用户手动发送。
- `FrameScheduler` 的待发送队列容量为 1；更新时覆盖尚未开始发送的旧帧。
- 屏幕正刷新时继续采集和分析 ECG。收到设备 `DONE` 后，只发送当时最新的一帧，不补发中间历史帧。
- framebuffer 和关键显示字段均未变化时不发送。
- 维护性全刷由 Quote/0 固件最终决定；PC 只提供普通 `auto` 与明确的手动 `force_full` 请求。

### 7. BLE Display Protocol v1 客户端要求

Quote/0 仓库中的 `docs/PROTOCOL_BLE_DISPLAY_V1.md` 是线协议规范原件，固定服务 UUID、特征 UUID、字节序、错误码和黄金向量。`smart-neckband` 保存兼容副本 `docs/protocol/quote0_ble_display_v1.md`，头部记录规范原件的 Git commit 和协议版本；PC 仓库原样复制固件的机器可读黄金向量，并由 C/Python 测试防止漂移。客户端至少支持：

- `DEVICE_INFO`：读取协议版本、分辨率、frame bytes、固件版本和能力。
- `CONTROL` write-with-response：只支持 `BEGIN_FRAME`、`COMMIT_FRAME`、`CANCEL_FRAME`；强制全刷通过 BEGIN 的 `refresh_request=1` 请求。
- `FRAME_DATA` write-with-response：包含 frame ID、顺序 offset 和数据块；默认块大小保守设为 180 字节，并根据实际 ATT MTU 上限调整。
- `STATUS` read/notify：`READY`、`RECEIVING`、`QUEUED`、`REFRESHING`、`DONE`、`ERROR`，不使用控制命令轮询状态。

`BEGIN_FRAME` 元数据至少包含协议版本、frame ID、5,624 字节长度、CRC32、旋转、刷新请求、源 ECG 样本索引和源设备单调时间戳。所有多字节整数采用 little-endian。设备必须在 `DONE` 中通过 `last_frame_id` 回显完成帧，并报告实际刷新模式、变化区域、刷新耗时和局刷计数；接收中的事务使用 `active_frame_id`。

### 8. 连接和安全

- 扫描只匹配 Quote/0 专用服务 UUID或 `InkCanvas-Quote0-` 名称，不匹配 `CollarC3-`。
- 连接必须使用加密和绑定，客户端处理 Windows GATT 缓存与首次配对等待。
- 未加密时不得发送 ECG 图像或健康指标。
- 日志可以记录 frame ID、长度、CRC、状态和错误，但不得记录 ECG 样本值、完整 framebuffer 或配对密钥。

## Work breakdown

1. 从 Quote/0 规范原件建立 BLE Display Protocol v1 兼容副本、Python 编解码器和黄金向量；先用纯内存测试固定帧头、块、状态和 CRC，并校验规范源 commit。
2. 实现 Quote/0 扫描和 `EpaperDisplayClient` 生命周期，使用假 Bleak client 测试连接、配对、重连、状态通知和有界写队列。
3. 实现纯函数 296×152 framebuffer、星形图标、文本布局、HRV/SQI/导联状态渲染和 8 秒 ECG min/max 包络。
4. 实现 30 秒 RR/NN 窗口和 HRV 有效性门限，复用项目已有 RMSSD 算法并补充边界测试。
5. 实现 `DisplayStateBuilder` 和 `FrameScheduler`，覆盖节流、优先事件、无变化跳过、队列容量 1 和 stale 数据处理。
6. 新增 `EpaperSyncPanel`，将扫描、连接、预览、同步设置、设备状态和折叠日志集成到主 GUI。
7. 将 panel 生命周期接入 `MainWindow.close()`，确保电子纸客户端停止不影响项圈 reader、Health、麦克风和其他页签。
8. 与 Quote/0 固件联调 BEGIN/CHUNK/COMMIT/STATUS，验证 CRC 错误、超时、断连、重连和刷新期间新帧覆盖。
9. 更新上位机用户文档、协议文档和两设备操作步骤。

## Validation

自动化验证：

```powershell
.\tools\project.ps1 pc-test
git diff --check
git status --short --branch
```

聚焦测试至少覆盖：

- 296×152 帧长度恒为 5,624 字节，像素位序和 CRC 黄金向量正确。
- 固定布局不显示 `HR` 和更新时间，显示星形图标、BPM、HRV、导联、最近 8 秒、SQI 和大波形区。
- 用户标注的 SQRT 被实现为现有 SQI 指标，没有创造未定义健康指标。
- 波形 min/max 包络能保留窄峰，输入 NaN、常量、短窗口和缺数据时安全退化。
- HRV 只有在 30 秒窗口、有效 NN 数和质量门限通过后显示。
- Qt offscreen 构造、页签名称、控件交互、关闭生命周期和默认折叠日志。
- 假 BLE 客户端下的分块、offset、CRC、状态机、超时和重连。
- 项圈数据持续进入时，等待 Quote/0 约 4.1 秒刷新不会阻塞 `BlePacketReader`。

双设备电子台架验证：

1. 项圈不接人体电极，通过加密 BLE 向 PC 连续发送测试 ECG。
2. PC 同时连接 Quote/0，连续运行至少 30 分钟，默认每 15 秒尝试同步。
3. 核对屏幕帧对应的源样本索引、HR、HRV、SQI 和导联状态。
4. 在 Quote/0 刷新期间确认项圈 packet count 持续增长，CRC 错误和 packet loss 不因显示操作增加。
5. 测试 Quote/0 断电、重启、断连、重新绑定以及项圈单独断连。
6. 记录局刷/全刷模式、残影、刷新耗时和电池趋势；不据此宣称人体或医疗验证。

人体 ECG 验证只有在用户以后明确授权时才可进行。届时项圈必须独立电池供电并只用无线链路，电极连接人体时不得连接桌面 USB、墙充、充电中的移动电源或接地台式仪器。

## Risks and rollback

- Windows 同时维护两个 Bleak client 可能暴露 WinRT GATT 缓存或线程 apartment 问题。客户端必须分别记录连接阶段；如独立事件循环不稳定，回滚到共享 BLE runtime，而不是在 GUI 线程运行异步 I/O。
- 电子纸刷新慢，发送过快会造成队列堆积。容量 1 和最新帧覆盖是硬约束。
- 过度压缩 Y 轴可能放大噪声；波形只作预览，必须显示 SQI/导联状态且不得用于诊断。
- HRV 容易因窗口、伪峰或运动失真。无效时显示 `--`，并保留有效性原因供 GUI 查看。
- 字体差异会导致 PC 预览与实际 framebuffer 不一致。渲染使用随项目提交的确定性像素字体或路径资源。
- 回滚通过普通 revert 或停用“自动同步”完成；不得改变项圈原始采集协议或删除现有 GUI 页面。

## Progress

- [x] 固定并评审 BLE Display Protocol v1 PC 兼容草案。
- [x] 实现显示客户端与测试替身。
- [x] 实现固定布局和 1-bit framebuffer。
- [x] 实现 30 秒 HRV 状态构建。
- [x] 实现最新帧调度器。
- [x] 集成“墨水屏同步”Qt 页签。
- [x] 完成自动化测试。
- [x] 按固件规范原件和黄金向量修复 BLE 协议漂移。
- [ ] 完成双 BLE 台架联调。
- [x] 更新文档并记录未验证项。

## Discoveries

- Quote/0 的单帧只有 5,624 字节，BLE 分块传输不是主要延迟；约 4.1 秒的 UC8251D 刷新才是节奏上限。
- 当前 PC 实时分析已经提供 HR、RR 和 SQI，但 HRV 需要从 RR observations 建立独立的 30 秒有效窗口。
- 将电子纸客户端后端解耦与把功能集成到同一个 Qt GUI 并不冲突；页签负责控制和展示，客户端仍保持独立线程、状态和错误边界。
- 当前屏幕需求中的 “SQRT” 与仓库指标不对应，计划按现有 `SQI` 实现，避免创造含义不明的健康量。
- Quote/0 固件规范原件已固定在 commit `cbb351deb634c6463e0e85cd271916f51f87e349`。早期 PC 草案使用了错误的 `7f5100xx` UUID、不同的字段顺序和长度，并错误加入 `GET_STATUS`/`FORCE_FULL_NEXT`；现已按固件 JSON 原样复制黄金向量并逐字节验证六类消息。
- PySide6 字体渲染必须运行在现有 `QApplication` 生命周期内；测试使用 offscreen QApplication，渲染器不自行创建第二个 Qt 应用。
- STATUS notification 订阅成功不代表 Windows 链路已经加密。客户端必须在任何 CONTROL/FRAME_DATA 写入前轮询 STATUS，直到 `LINK_ENCRYPTED` 与 `LINK_BONDED` 同时置位。BEGIN 遇到 GATT error 5 时重新读取 STATUS：若安全标志已经齐全，直接报告固件安全策略冲突（例如 SC Only 与 Just Works 不兼容），不做无效重配对；只有标志确实掉线时才重新配对并重试一次。认证失败期间不得发送数据块。
- 真机出现 `flags=0x0027`（encrypted+bonded+connected+sync）但 CONTROL 仍返回 ATT 0x05，已定位为固件同时启用 `CONFIG_BT_NIMBLE_SM_SC_ONLY=1`、NoInputNoOutput 和 `sm_mitm=0`：Just Works 链路无法满足 NimBLE SC Only 对 authenticated Level 4 的要求。PC 只能明确诊断，根治需要固件关闭 SC Only，同时继续保留 characteristic 加密写权限。
- 协议、显示状态/渲染、BLE 客户端和 Qt 页签共有 28 项聚焦测试通过；完整 `pc-test` 为 260 项全部通过。真实双设备联调仍待进行。

## Result

PC 端计划内的软件工作已完成：新增 BLE Display v1 兼容副本和固件黄金向量、30 秒去重 HRV 窗口、296×152 1-bit 固定布局与 ECG min/max 包络、容量为 1 的最新帧调度、独立 Quote/0 Bleak 客户端、瞬时失败 CANCEL/单次重试、自动重连，以及主 Qt GUI 的“墨水屏同步”页和操作文档。协议实现已对齐固件 commit `cbb351deb634c6463e0e85cd271916f51f87e349` 的 UUID、控制消息、帧数据头、DEVICE_INFO、STATUS、能力位、状态位和错误码。BLE 发送循环现在把 encrypted+bonded STATUS 作为硬门槛；链路标志掉线时执行一次受控重配对重试，标志齐全却仍返回错误 5 时明确指向固件 SC Only/Just Works 策略冲突。

验证结果：28 项电子纸聚焦测试通过；仓库完整 `pc-test` 为 `260 passed in 50.35s`；`git diff --check` 待最终提交前再次确认。一次无关的本地 HTTP 重定向测试曾因 Windows `ConnectionAbortedError` 抖动失败，单测复跑和完整套件复跑均通过。没有修改任何固件、GPIO、采样率或 V0 原始协议。

尚未验证：修复后的上位机与 Quote/0 真机完整帧传输、Windows 同时连接两个真实 Peripheral、约 4.1 秒屏幕刷新期间的项圈持续吞吐、残影、刷新策略、电池功耗、绑定缓存和人体连接行为。
