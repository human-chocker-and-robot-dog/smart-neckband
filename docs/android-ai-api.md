# 手机直连 AI Insight

手机直接调用 DeepSeek 官方或 OpenAI 兼容的 Chat Completions API。
0.1.6 把 Insight 改为日常身体解读：**身体节奏、活动与休息、此刻建议**三张卡片。
已计算出的 HRV 参考值会参与解读，使用 `≈` 标记；缺失指标直接省略。
硬件排查信息保留在数据详情与 Settings，不再成为 Insight 的输入依据或主题。

## 使用

1. Settings 关闭 AI Insight、ECG / 心率两个演示开关。
2. 选择「DeepSeek 官方」，填写个人 API Key，开启「启用 AI Insight」并保存。
   预设地址 `https://api.deepseek.com`，预设模型 `deepseek-flash`，模型 ID 可修改。
3. 可点「用示例检查 API」查看三张合成示例卡片。这会实际调用 API、消耗额度，
   不会将合成记录存进真实历史。此按钮与普通离线演示模式分开。
4. 真实采集时进入 AI Insight，点「读懂此刻」。短窗口 HRV 参考值也可直接解读。
5. 可单独开启「自动生成解释」并保存。自动生成可以使用普通记录或参考值，
   无需先积累完整 60 秒 HRV；底层采集、HRV 算法与原始数据不受影响。

兼容服务接受基础地址、带 `/v1` 等前缀的地址，或完整 `/chat/completions` 地址。
模型需填写服务商提供的 ID。JSON 模式默认开启；服务不支持 `response_format`
时可关闭，但本地响应校验仍然生效。可切换 `max_completion_tokens` / `max_tokens`，
输出上限均为 2048 token。DeepSeek 官方预设使用 `max_tokens` 并关闭 thinking。

## 提示词与呈现

完整实际提示词位于
[InsightPrompt.kt](../android_app/app/src/main/java/com/smartneckband/companion/data/InsightPrompt.kt)。
关键约定：已有指标直接解读，参考值使用温和语气；缺失字段跳过，不补零或编数值；
正文围绕身体节奏与日常行动，不解释“暂不可用”、电极、削顶、采样等技术问题。
保持单次记录的范围，不编造个人基线、前后变化、情绪、压力等级或诊断。

一次请求生成三个互补主题，按顺序展示。绿色节奏卡、蓝色活动卡、暖色建议卡
分别呈现短文、数据标签和一个轻量行动。数字标签从本机事件映射；时间、来源和
参考说明按组显示。旧版历史保持原文，收进默认折叠的「过往记录」，不会再次占据
新页面默认视图。普通演示和本地解读也使用这一套三卡片结构。

## 结构化契约 v2

发送 `POST /chat/completions`，Bearer 身份验证，`stream: false`。
system 使用上述提示词；user 是程序生成的单次记录。只包含当前新鲜且有限的
数值，缺失项从 metrics 和 evidence 同时省略；有至少一个指标才生成记录。
不会再次用 60 秒静息资格或采样告警过滤手机已经显示的参考值。

```json
{
  "schema_version": 2,
  "id": "事件 UUID",
  "type": "body.rest_window",
  "source": "collar_ecg_imu",
  "synthetic": false,
  "observed_at": "ISO-8601 时间",
  "metrics": {"heart_rate_bpm": 72, "hrv_rmssd_ms": 42, "motion_score": 6, "still_ratio_percent": 94},
  "context": {"comparison": "single_snapshot", "reference_metrics": ["hrv_rmssd_ms"],
    "hrv_window_seconds": 8.2, "hrv_rr_count": 10},
  "evidence": {"heart_rate_bpm": "心率 72.0 BPM", "hrv_rmssd_ms": "RMSSD ≈ 42.0 ms",
    "motion_score": "活动指数 6.0", "still_ratio_percent": "静止占比 94.0%"}
}
```

`body.movement` / `body.rest_window` / `body.observation` 仅用于本次场景和去重，
`rest_window` 在此表示活动幅度较小，不等于完整 60 秒 HRV 已就绪。
使用当前显示的 HRV（完整值优先，其次参考值），在 context 明确标记来源性质。
不向模型发送原始 ECG、音频、设备标识、硬件错误说明或历史卡片。

仅解析 `choices[0].message.content`，须是一个符合以下结构的 JSON 对象：

```json
{
  "schema_version": 2,
  "event_id": "与输入 id 相同",
  "cards": [
    {"kind": "rhythm", "title": "听见此刻的节奏", "explanation": "根据本次心率与 HRV 写一段日常观察。",
      "suggestion": "", "evidence_ids": ["heart_rate_bpm", "hrv_rmssd_ms"]},
    {"kind": "activity", "title": "留一点安静的空间", "explanation": "结合本次活动记录写一段动静与休息的观察。",
      "suggestion": "", "evidence_ids": ["motion_score"]},
    {"kind": "suggestion", "title": "给自己一分钟", "explanation": "围绕本次记录给出一个温和提醒。",
      "suggestion": "放松肩膀，自然呼吸一分钟。", "evidence_ids": ["heart_rate_bpm"]}
  ]
}
```

根对象三个字段必须齐全、无额外字段；版本为整数 2，事件 ID 必须匹配。
恰好三张卡，每种 kind 各一张，客户端按 rhythm/activity/suggestion 排序。
每张五个字段必须齐全；标题 1–30 字符，解释 1–240，建议最多 100 字符（行动卡
不能为空）；每张引用 1–3 个实际存在且不重复的 evidence 键。参考标记和展示数字
由本机映射。三张卡一次写入列表与持久化，最多保留 30 张。

拒绝空内容、截断、拒答、工具调用、错误类型、未知依据、额外字段、重复主题和
典型硬件排查/缺失数据叙事；校验失败时显示明确标注为「本地解读」的日常卡片，
API 错误详情留在 Settings。结构和主题检查无法保证模型自由文本的事实完全正确。
只渲染普通文本，不执行代码、链接或工具调用。

## 请求与密钥

网络功能、自动生成均默认关闭。普通演示从不请求 API，任一演示开关开启时
取消等待中的请求；已经发到服务商的请求无法撤回，不能保证免除已经发生的费用。
手动请求至少间隔 10 秒；自动至少 1 分钟，同类场景至少 5 分钟。只进行一个请求，
没有自动 HTTP 重试。失败会暂停自动 AI（跨重启保留），手动成功或重新保存配置恢复。

连接超时 10 秒、读取超时 30 秒、总等待 60 秒，响应最多 64 KiB。
错误消息只含本地说明及 HTTP 状态，不展示服务端错误正文，也不记录提示/响应。
凭据、API 配置不会进入传感器诊断捕获或分享文件。

密钥使用 Android Keystore AES-256-GCM 加密，绑定规范化后的完整接口地址。
修改地址必须重新填写密钥。拒绝 HTTP、地址内凭据、查询参数、片段和重定向。
APK 不包含公共密钥，应用备份关闭。删除密钥会关闭 AI；卸载会丢失本机配置。
升级保留个人 API 配置和历史记录，不恢复旧版网关设置。

## 本次验证与手动验收

按用户要求只进行源码检查与 `:app:assembleDebug`，不运行自动测试、自动导航
或真实付费 API。由用户在手机上检查：

- 有 `≈ RMSSD` 时点「读懂此刻」，出现三张简短身体解读，数值标签保留参考标记。
- HRV 数值确实缺失时，只读已有心率/活动，不生成 HRV 缺失或硬件排查卡片。
- 自动生成同样能使用参考 HRV；关闭网络时，本地卡片保持日常主题并标明来源。
- 进入演示模式查看新样式；旧历史需展开，升级后 API Key 无需重新输入。

## 协议参考（2026-09-29 查阅）

- [DeepSeek 首次调用](https://api-docs.deepseek.com/guides/harness)
- [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)
- [DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)
- [OpenAI Structured Outputs 与 JSON mode](https://developers.openai.com/api/docs/guides/structured-outputs)
