# 演示与波形诊断（0.1.2）

## 两个独立演示开关

在手机 **Settings → 演示模式**：

- **AI Insight 演示**：立即显示 4 张手工预设卡片，覆盖静息、活动、活动后变化、信号不足。每张包含解释、示例依据、限制和“演示”来源；不请求 API。
- **ECG / 心率演示**：Today / Detail 显示连续滚动的合成 ECG、72 BPM 心率、42 ms 示例 RMSSD 和示例活动指标。无需蓝牙，也无需等待分析窗口。

开关独立保存，退出再进入仍保持。关闭后恢复真实数据展示，不把演示数据写入 repository、原始日志、历史卡片或 AI 请求。已运行的真实采集不会被开关偷偷停止，Settings 的“真实采集”仍控制设备，后台通知仍是真实指标。ECG 演示开启时，下一次打开 App 不自动连接设备；关闭后可点“开始采集”，之后新启动恢复原有自动采集策略。

演示卡片是产品样例，不是模型已生成的结论。真实手机直连 DeepSeek 等 API 留到下一阶段：结构化事件 JSON、来源/质量门控、响应 schema 校验、失败处理、密钥本地保护和提供方实测都需要独立验收。旧网关兼容代码保留但设置入口暂不展示；任一演示开启时不生成新的事件/API 请求。

## 捕获当前问题

电脑端更新代码后重新打开上位机，工具栏点击 **捕获诊断（最近 60 秒）**。文件写入启动工作目录下的 `data/diagnostics/pc-*.jsonl`，弹窗和 DEBUG 区显示完整路径。已有实验记录功能不变。

手机端 **Settings → 波形诊断 → 捕获诊断**，保存 `files/diagnostics/latest.jsonl`，再次捕获会替换它。可以点“分享最近一次捕获”由用户选择接收位置，或在已授权 USB 调试的手机上运行：

```powershell
.\tools\collect-phone-diagnostics.ps1
# 多个设备时显式选择，例如：
.\tools\collect-phone-diagnostics.ps1 -Serial 40c87980
```

脚本将文件放到仓库 `data/diagnostics/phone-*.jsonl`。这些文件被 Git 忽略，Codex 可直接读取本地路径。程序不自动上传，也不把原始数据刷入 logcat。输入来自实际接收通道，包含传感器数据；若流中包含麦克风帧，原始字节也会包含这些帧。

两端默认只在内存中保存最近 60 秒，最多 16 MiB；超过限制淘汰最旧记录并记录数量。未连接设备时捕获可能只有元信息。内存日志不跨进程退出；已保存文件仍保留。手机保留最近一个文件，PC 每次新建文件，按需自行清理。

## 同一份数据回放与两端比较

从仓库根目录、在已安装科学计算依赖的 Python 环境执行：

```powershell
$env:PYTHONPATH = 'pc_app/src'
python -m smart_neckband.diagnostics data/diagnostics/phone-example.jsonl `
  --android-source android_app/app/src/main/python `
  --output data/diagnostics/report.json

python -m smart_neckband.diagnostics data/diagnostics/pc-example.jsonl `
  --compare data/diagnostics/phone-example.jsonl `
  --output data/diagnostics/comparison.json
```

第一条会用 PC 解码器重放保存的原始分片，与手机 Kotlin 解码记录逐帧比较，再把同一份样本输入手机 Python adapter 和 PC 分析函数。手机日志还记录处理队列实际提交边界，因此可对齐最后一次分析，检查界面输出的原始数组是否与回放完全一致。

报告列出 CRC、样本编号缺口、flags、已比对的帧数、解码差异、原始/分析/滤波点数、HR、SQI、滤波最大差值和运行依赖版本。`native_frames_without_retained_wire` 可能是环形日志从半包开始，不应直接当作解码错误。`recorded_phone_raw_equals_replay` 只有在日志保留完整窗口且数据未过期时才应为真。

第二条只按设备样本编号和时间戳寻找候选重叠。没有重叠会明确输出 `no_overlap`，不会把先后采集的不同身体信号强行比较。即使编号/时间相同，也应确认设备相同且中间没有重启；最可靠的算法定位方式是第一条的单份数据回放。

离线回放在所选电脑 Python 环境运行，隔离的是算法与窗口策略，**不模拟 Android 线程调度、BLE 丢包或 native 库环境**。电脑当前 NeuroKit2 0.2.13，手机 0.2.10；比较 SQI 时必须看版本。手机可能因接触、溢出、时序、过期门控而主动隐藏指标，清洗一致但指标不同不必然是 bug。Android 仪器测试另行验证 Kotlin → Chaquopy → Compose 路径。

## JSONL schema 1

首行 `meta`：平台、来源、捕获时间、版本、保留时间、淘汰记录数。其后每行包含 `type`、`elapsed_ms`、`connection_id`、`data`。单调时钟只用于同一主机内排序，不直接跨主机相减。

| type | 含义 |
|---|---|
| transport | 接收的原始分片，十六进制 hex，未过滤 |
| frame | 手机实际解码结果、flags、序号、设备时间戳、原样样本、解析统计、是否在接收状态 |
| processed | 手机实际送入 Python 的 batch 及排队延迟 |
| analysis | 手机分析输出，含原始/滤波数组和窗口起止编号 |
| state | 连接尝试、断开、错误和停止 |
| pc_snapshot | 捕获时 PC 原始窗口、异步分析结果及分析截止编号、解析统计 |

人体 ECG 采集仍遵循仓库安全规则：颈环独立电池供电并走无线链路，不能把接人体电极的颈环连到电脑 USB、墙电或接地仪器。日志工具不主动连接或触发采集。

## 本次交付的验证范围

0.1.2 已覆盖安装到小米 13 Ultra，保留应用数据。构建、lint、10 项 JVM 测试、17 项手机 Python/回放测试和 226 项 PC 测试已通过。小米原生算法测试完成，界面与捕获导出实机测试未完成；按用户要求已停止自动测试并移除独立测试 APK，由用户手动验收。合成数据回放通过不等于真实 BLE 波形已经恢复。
