# 墨水屏布局与分层刷新计划

## Goal

把 Quote/0 的 296×152 健康摘要改成更适合真机阅读的两行大字布局：第一行使用心脏图标、左侧 BPM 和右侧同字号 HRV；第二行放紧凑导联状态、`ECG 最近 8 秒` 和 SQI。扩大 ECG 波形区并避免窄 R 峰被百分位缩放截断。

在不修改 BLE Display v1 协议的前提下，将顶部指标与 ECG 波形更新节奏解耦：顶部 BPM/HRV/SQI/导联使用约 5 秒的最短安全发送间隔，ECG 波形快照默认每 10 秒更新。指标帧保持波形像素不变，使 Quote/0 固件的 framebuffer diff 能把变化限制在顶部区域并选择局部刷新。PC 不在设备忙碌时积压帧；只在设备空闲的实际派发时刻读取最新指标和最近 8 秒 ECG。

## Current state

- `render_epaper_frame()` 当前依次绘制星形+BPM、单独 HRV、导联、ECG/SQI，共占约 80 像素；波形只剩约 70 像素高度。
- 波形使用每列 min/max 垂直线，并按 2%/98% 分位缩放；窄 R 峰可能落在被截断的极值中，画面也容易过密。
- GUI 每约 0.5 秒构造包含最新波形的完整状态，自动调度器按单一 10–60 秒间隔发送。
- 每个自动帧都可能同时改变顶部和波形，因此即使固件支持局刷，changed rectangle 也通常覆盖大部分屏幕。
- Quote/0 真机刷新约 4.1 秒；PC 不能把物理显示承诺为 1 Hz。指标默认间隔采用 5 秒，设备忙碌期间跳过派发而不是保存 pending 帧。
- BLE 协议仍发送完整 5,624 字节 framebuffer；实际 none/full/partial 和维护性全刷由固件决定。

## Scope

Included:

- 将五角星替换为确定性黑白心脏图标。
- 第一行左 BPM、右 HRV，使用相同主字号。
- 第二行放大紧凑导联状态、ECG 最近 8 秒和 SQI。
- 扩大波形高度，调整包络绘制密度并使用完整有限范围缩放。
- 增加可测试的 ECG 波形快照节奏，默认 10 秒。
- 将普通指标帧最短发送间隔改为默认 5 秒，并在 GUI 中分别配置指标与 ECG 间隔。
- 客户端只允许一个正在交接或活动的 PC 帧；忙碌时拒绝新帧，失败帧不自动重排。
- 更新自动化测试、用户文档和现有电子纸计划结果。

Excluded:

- 修改 Quote/0 固件、LUT、局刷算法或维护性全刷阈值。
- 修改 BLE UUID、消息结构、CRC、加密或绑定行为。
- 把物理 UC8251D 刷新宣传为 1 Hz。
- 修改项圈原始 ECG 采样、传输、缓存或记录数据。

## Design decisions

### 1. 固定布局

- 第一行约 `y=0..32`：心脏图标；左侧 `<BPM> BPM`；右侧 `HRV <ms> ms`，相同像素字号和粗体。
- 第二行约 `y=33..58`：紧凑导联状态、`ECG 最近 8 秒`、`SQI <percent>%`，使用更大的粗体。
- 波形从约 `y=61` 延伸到屏幕底部，获得约 89 像素高度。

### 2. 波形可读性

- 仍使用 8 秒清洗 ECG 和 min/max envelope，保持窄峰信息。
- Y 轴使用所有有限 envelope 极值加少量边距，不再用 2%/98% 分位截断峰值。
- 减少密集的逐列竖线，并用连接轨迹维持连续感，降低黑色堆积。

### 3. 分层刷新

- `EpaperWaveformSnapshotter` 分离候选与提交：只有帧真正被 BLE 客户端接受派发后，才保存当前显示波形并推进 10 秒计时。
- 状态中的 BPM、HRV、SQI 和导联仍使用最新分析结果；中间渲染帧复用旧波形。
- `EpaperFrameScheduler` 默认每 5 秒允许一个变化帧。固件刷新约 4.1 秒，因此这是当前可诚实承诺的最短安全节奏。
- ECG 默认每 10 秒变化一次；其余指标帧只有顶部区域变化，从而为固件局刷提供小 changed rectangle。
- 手动“立即发送”和“强制全刷”使用当前最新波形。
- 设备状态为 receiving/queued/refreshing，或 PC 已有 pending/active 交接时，不生成 ScheduledFrame。恢复空闲后从最新分析重新渲染。
- 传输失败只取消当前事务，不自动恢复旧 `_FrameRequest`；下一轮调度使用最新数据。

## Work breakdown

1. 实现心脏图标、两行大字布局和扩大波形区域。
2. 调整波形缩放与绘制密度，补充完整峰值与像素密度测试。
3. 实现 `EpaperWaveformSnapshotter`，覆盖首次、未到期、到期、来源变化和强制更新。
4. 修改 Qt 页面，分别配置指标间隔和 ECG 更新间隔，默认 5 秒与 10 秒。
5. 更新渲染、调度和 GUI 测试。
6. 更新用户文档和计划结果，运行电子纸聚焦测试及完整 `pc-test`。
7. 审查差异、提交、推送并重启上位机。

## Validation

```powershell
.\pc_app\.venv\Scripts\python.exe -m pytest pc_app\tests\test_epaper_sync.py pc_app\tests\test_epaper_ui.py -q
.\pc_app\.venv\Scripts\python.exe -m pytest pc_app\tests\test_epaper_protocol.py pc_app\tests\test_epaper_ble.py pc_app\tests\test_epaper_sync.py pc_app\tests\test_epaper_ui.py -q
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\project.ps1 pc-test
git diff --check
```

真机验收：观察连续指标变化时 STATUS 的 `actual_refresh_mode=PARTIAL` 和 changed region 位于顶部；每约 10 秒波形区域才发生变化。设备忙碌期间 PC pending 保持为空，恢复空闲后发送帧的源样本和波形必须来自当时最新分析。不宣称达到 1 Hz。

## Risks and rollback

- 5 秒指标间隔仍接近真机刷新极限；若 BLE 传输和刷新总耗时超过间隔，中间时刻不生成帧，空闲后直接发送最新状态。
- 固件可能因维护阈值执行全刷，即使 PC 只改变顶部区域；PC 不覆盖固件维护策略。
- 完整 min/max 范围可能被单个噪声尖峰拉宽；优先保证波形不截断，后续根据真机记录再选择稳健缩放。
- 回滚可恢复旧布局、单一调度间隔和每次使用最新波形；不需要修改协议或固件。

## Progress

- [x] 完成新布局和心脏图标。
- [x] 完成波形可读性调整。
- [x] 完成指标/波形分层节奏。
- [x] 完成 GUI 控件与测试。
- [x] 完成文档和全部验证。
- [x] 提交、推送并重启上位机。

## Discoveries

- 用户口述的 HRB 按既有指标解释为 HRV，SQL 按既有质量指标解释为 SQI。
- 电子纸约 4.1 秒刷新决定了物理显示不可能可靠达到 1 Hz；默认 5 秒是当前最短安全节奏。
- Qt offscreen 测试后端不枚举 Windows 系统字体，因此会把文字渲染为方框；使用真实 Windows Qt 后端生成的 296×152 预览确认 `Microsoft YaHei UI` 正常解析，心脏、BPM、HRV、第二行状态/SQI 和波形均可读。
- 旧波形按 2%/98% 分位缩放会截掉占比很低的窄 R 峰。新实现使用完整有限 envelope 范围和 4% 边距，并以连续中线加稀疏/显著 min-max 竖线降低黑色密度。
- 首版分层刷新在预览阶段就推进波形计时，并允许客户端覆盖 pending；真机忙碌时会导致实际发送旧快照。修正后所有节奏都以实际派发为提交点，PC 不积压或重试旧帧。

## Result

已完成心脏图标、第一行左右同字号 BPM/HRV、第二行放大状态/ECG/SQI、约 89 像素高波形区，以及完整峰值范围绘制。`EpaperWaveformSnapshotter` 只在实际派发时提交快照：顶部指标默认每 5 秒允许发送，ECG 波形默认每 10 秒替换；中间帧保持上次已发送波形不变，为固件 auto diff 的顶部局刷提供小变化区域。GUI 分别暴露“指标局刷间隔”和“ECG 更新间隔”。客户端在设备忙碌时不保存帧，恢复空闲后重新渲染最新数据。

验证结果：真实 Windows Qt 后端的 296×152 像素预览已目视检查；31 项电子纸聚焦测试通过；完整 `pc-test` 为 `263 passed in 67.10s`。零积压 follow-up 待本轮提交和重启后完成记录。没有修改固件、BLE 协议或项圈原始 ECG 数据链。真机实际 partial changed region、长期残影和功耗仍需运行观察。
