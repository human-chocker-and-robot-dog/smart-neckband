# 手机直连 AI Insight

从 Android 0.1.4 开始，手机可直接调用 DeepSeek 官方或 OpenAI 兼容的
Chat Completions 接口，无需另建网关。内置演示卡片仍可独立使用。

## 配置与使用

1. 在 Settings 关闭 AI Insight、ECG / 心率两个演示开关。
2. 在「AI API 接入」选择「DeepSeek 官方」。预设地址为
   `https://api.deepseek.com`，模型为 `deepseek-flash`；模型 ID 可以修改。
3. 只在手机的 API Key 输入框填写自己的密钥，开启「启用 AI Insight」，
   点「保存 AI 配置」。保存后密钥不回填、不显示明文。
4. 可先点「用示例检查 API」：它会消耗 API 额度，使用固定的合成摘要，
   成功后在 Settings 显示临时卡片，不保存到真实记录。
5. 实时采集时进入 AI Insight，点「生成当前解释」。无需等待 HRV 就绪；
   不合格信号只能生成质量解释，不发送不可靠的心率或 HRV 数值。
6. 如需后台随有效事件生成卡片，开启「自动生成解释」并保存。

兼容服务支持基础地址（如 `https://api.openai.com/v1`）、其他版本前缀，
也接受完整的 `/chat/completions` 地址，不会重复追加路径。需填写服务商
实际提供的模型 ID。默认使用 `response_format: {"type":"json_object"}`；
不支持此参数的服务可关闭 JSON 模式，本地格式检查仍然生效。
「使用 max_completion_tokens」适配 OpenAI 新模型；旧兼容接口可关闭以发送
`max_tokens`。均限制为 2048 输出 token，不传 temperature 等可选参数。
DeepSeek 官方预设使用 `max_tokens`，并显式关闭 thinking。

## 数据与密钥

网络功能默认关闭，自动生成默认关闭。请求仅含本次结构化摘要、质量状态和
时间，不含原始 ADC、音频、蓝牙地址或个人身份标识。历史卡片不作为聊天上下文
重传。普通演示模式从不触发 API；开启任一演示开关会取消正在等待的请求。
已经发送到服务商的请求无法撤回，取消不会保证免除服务商已产生的费用。

密钥使用 Android Keystore AES-256-GCM 加密，密文绑定到规范化后的完整接口
地址。换地址必须重新输入密钥，不会把旧密钥自动发送给另一服务。拒绝 HTTP、
地址内凭据、查询参数、片段及所有重定向。APK 不包含公共密钥；应用备份关闭。
诊断捕获和分享只包含原有传感器数据，不包含 API 配置、密钥、提示或响应。
删除密钥会取消请求并关闭 AI。此方案面向个人密钥的 MVP，卸载应用会丢失配置。

## JSON 契约

发送 `POST /chat/completions`，`Authorization: Bearer <本机个人密钥>`，
`stream: false`。system 消息约束语言、长度、数据不足行为和非诊断范围；user
消息是一个 JSON 对象：

```json
{
  "schema_version": 1,
  "id": "事件 UUID",
  "type": "body.observation",
  "source": "collar_ecg_imu",
  "synthetic": false,
  "observed_at": "ISO-8601 时间",
  "metrics": {"heart_rate_bpm": 72, "hrv_rmssd_ms": null, "motion_score": null},
  "quality": {"signal_quality": 0.9, "lead_off": false, "timing_warning": false,
    "data_age_ms": 20, "hrv_status": "unavailable", "hrv_window_s": 10, "rr_count": 8},
  "limitations": ["HRV 尚未就绪", "没有历史基线，不能推断趋势"],
  "evidence": {"heart_rate_bpm": "本次心率 72.0 BPM", "hrv_status": "HRV 暂不可用"}
}
```

事件包括 `body.movement`、`body.rest_window`、手动 `body.observation` 和
手动 `body.signal_quality`。有效活动/静息事件才会自动触发。质量不合格时，
生理指标为 null；null 不代表零。RMSSD 必须通过完整静息质量检查才能发给 AI。
0.1.5 的界面可以显示短窗口 HRV 参考值，但该参考值不作为有效 RMSSD 发给 AI。
质量摘要额外提供实际 ADC 触顶点数和分析窗口点数。

仅解析 `choices[0].message.content`，其内容须为单个 JSON 对象：

```json
{
  "schema_version": 1,
  "event_id": "与输入 id 相同",
  "title": "正在积累静息记录",
  "explanation": "当前可以看到心率，但 HRV 尚未就绪。",
  "suggestion": "保持舒适静止，等待更多有效记录。",
  "evidence_ids": ["heart_rate_bpm", "hrv_status"]
}
```

六个字段必须齐全，无额外字段；版本为整数 1，事件 ID 必须匹配。
标题 1–80 字符，解释 1–1200，建议 1–400，依据键 1–6 个且不重复，必须来自
输入 evidence。卡片依据由本机原始摘要映射，不能由模型改写数值。提示禁止诊断、
药物建议、压力分数、虚构趋势和硬件控制。所有卡片附固定局限说明；结构校验
不能保证自由文本事实完全正确。输出只作为普通文本显示，不执行命令或工具。

要求 `finish_reason=stop`；拒绝截断、拒答、工具调用、空内容、格式错误和
超过 64 KiB 的响应。连接超时 10 秒、读取超时 30 秒、总等待 60 秒。
错误显示本地固定消息及 HTTP 状态，不展示可能含敏感信息的服务端错误正文。
每次失败保留明确标注的本地说明；合成 API 检查失败不写入真实历史。

一次只进行一个请求。手动至少间隔 10 秒；自动至少 1 分钟，同类事件至少
5 分钟。自动失败暂停后续网络请求（重启后仍暂停），需手动重试成功或重新保存配置恢复。
卡片最多保存 30 张，保留来源、时间、依据、建议与局限说明。

## 手动验收与本次验证范围

本次按用户要求只进行源码检查与 `:app:assembleDebug` 编译，没有运行单元测试、
仪器测试、自动导航或真实付费 API。以下由用户在手机上手动检查：

- 保存 DeepSeek 个人密钥后，用示例检查应出现带「API 合成示例」来源的卡片。
- 关闭并重新打开 App 后仍可调用；密钥框保持隐藏，不显示已存密钥。
- 真实采集中手动生成；HRV 暂缺时不补造数值，信号告警时只解释质量。
- 错误密钥显示 401，断网显示网络错误，原有真实数据采集不受影响。
- 换地址要求新密钥；删除密钥关闭 AI；演示模式与正在进行的 API 请求互斥。

## 官方协议参考（2026-09-29 查阅）

- [DeepSeek 首次调用](https://api-docs.deepseek.com/guides/harness)
- [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)
- [DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)
- [OpenAI Structured Outputs 与 JSON mode](https://developers.openai.com/api/docs/guides/structured-outputs)
