# Quote/0 电子墨水屏同步

主 Windows Qt 上位机包含“墨水屏同步”页面，可在继续接收项圈原始 ECG 的同时，通过第二条加密 BLE GATT 连接向 MindReset Quote/0 发送低频健康摘要画面。

当前实现完成了 PC 端协议、显示状态、HRV 窗口、1-bit 帧渲染、独立 BLE 客户端、最新帧队列和 Qt 页面。Quote/0 的 BLE Display v1 固件尚未实现，因此当前只能通过自动化测试和假 BLE client 验证，不能报告真实电子纸联调成功。

## 数据链路

```text
项圈 ESP32-C3
  └─ 500 Hz 原始 ECG + 状态
          │ 加密 BLE GATT
          ▼
Windows 主上位机
  ├─ 保留原始数据和记录链
  ├─ NeuroKit2 清洗、R 峰、HR、RR、SQI
  ├─ 30 秒有效 NN 窗口 RMSSD → 屏幕显示为 HRV
  ├─ 296×152 1-bit framebuffer
  └─ 独立 Quote/0 BLE client
          │ 加密 BLE Display v1
          ▼
Quote/0 ESP32-C3 + UC8251D
```

电子纸显示是独立预览分支，不修改项圈传输、缓存或记录的原始 ADC counts。

## 屏幕布局

画面固定为 296×152 横向、白底黑色：

1. 星形图标和 BPM，不显示单独的“HR”标题。
2. `HRV <value> ms`。技术计算是 30 秒超短时 RMSSD，屏幕不显示 RMSSD 缩写。
3. 导联正常、导联脱落、信号无效或等待数据。
4. `ECG · 最近 8 秒 · SQI <value>`。
5. 下方剩余区域显示最近 8 秒清洗 ECG 的 min/max 包络。

屏幕不显示更新时间。上位机页面显示源样本索引和数据年龄，便于诊断同步延迟。

## 使用方式

1. 用正常方式在“实时”页连接 `CollarC3-*` 项圈。
2. 打开“墨水屏同步”页。
3. 点击“扫描墨水屏”，选择 `InkCanvas-Quote0-*`。
4. 点击“连接墨水屏”。默认启用自动重连。
5. 确认页面预览、BPM、HRV、SQI 和导联状态符合预期。
6. 可点击“立即发送”测试普通 auto 刷新，或点击“下一帧强制全刷”。
7. 勾选“启用自动同步”后，上位机按默认最短 15 秒间隔调度画面；可配置为 10–60 秒。

自动同步默认关闭，避免在 Quote/0 固件未就绪或用户只想预览时反复连接和写入。

## HRV 和质量门限

- RR observations 按来源实例和结束样本索引去重，避免 10 秒重叠分析重复计数。
- 窗口长度为 30 秒。
- 至少需要 5 个有效 NN intervals。
- SQI 必须至少为 0.5。
- 导联脱落、严重 ADC clipping、分析超过 2 秒未更新或分析状态异常时 HRV 显示 `--`。
- 导联脱落或 clipping 会清空当前 HRV 窗口，恢复后重新积累。

这是超短时工程估计，不是医疗诊断结果。

## 刷新和队列

- PC 每约 0.5 秒更新内部分析和预览，但不会按该频率写电子纸。
- 普通自动帧默认最短间隔 15 秒。
- 导联状态、SQI 有效性、clipping 或 stale 状态变化可以优先调度下一帧。
- framebuffer 未变化时跳过发送。
- Quote/0 刷新期间只保留一张最新 pending 帧，旧 pending 帧被覆盖，不补发历史画面。
- PC 只请求 auto 或明确 force full；实际 none/full/partial 和维护性全刷由 Quote/0 固件决定。

## BLE Display v1

PC 兼容草案和黄金向量位于：

- `docs/protocol/quote0_ble_display_v1.md`
- `docs/protocol/quote0_ble_display_v1_golden_vectors.json`

协议使用专用服务，不复用项圈 Nordic UART Service。帧固定为 5,624 字节，分块默认 180 字节，所有写入使用 write-with-response。每个已接受的 COMMIT 必须由固件通过 STATUS notification 返回 DONE 或 ERROR。

Quote/0 固件采用协议后，应把其规范原件 commit 写回兼容文档和黄金向量元数据。

## 安全与验证边界

- Quote/0 写特征必须要求加密，设备信息必须声明 encrypted-write capability。
- 日志不记录 ECG 样本值、完整 framebuffer 或配对密钥。
- 当前没有刷写 Quote/0，也没有进行真实双 BLE 或电子纸残影测试。
- 人体 ECG 采集时项圈必须独立电池供电并仅用无线传输；电极连接人体时不得连接桌面 USB、墙充、充电中的移动电源或接地台式仪器。
