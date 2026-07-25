# Health MCP Agent 调用指南

本文面向运行在 CPE / RDK 上的 Agent 或 Gateway。Windows 上位机负责采集、
分析和保存派生健康数据；Agent 只通过 MCP 读取最近一段时间的心率、HRV 和
IMU 运动状态。

## 1. 连接信息

| 项目 | 值 |
|---|---|
| Transport | MCP Streamable HTTP |
| URL | `http://<CPE 可访问的 Windows 地址>:8765/mcp` |
| MCP protocol | `2025-11-25` |
| Authentication | 无；由 CPE 私有内网负责访问控制 |
| Content | MCP JSON-RPC，由 MCP SDK 处理 |

不要把 `/mcp` 当作普通 REST 接口直接拼接工具名。Agent 应使用支持
Streamable HTTP 的 MCP 客户端，完成 `initialize`、`tools/list` 和
`tools/call`。不要发送 Bearer Token，也不要把端口暴露到公网。

常见 Agent 配置可表达为以下结构；具体字段名按 Agent 框架调整：

```json
{
  "mcpServers": {
    "smart-collar-health": {
      "type": "streamable-http",
      "url": "http://192.168.1.20:8765/mcp"
    }
  }
}
```

服务端只公开三个只读、幂等、非破坏性工具：

```text
health.get_heart_rate
health.get_hrv
health.get_imu_state
```

## 2. Python MCP 客户端示例

下面的代码使用官方 `mcp` Python SDK。URL 应替换为 CPE 实际转发或可访问
的 Windows 地址。

```python
import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


MCP_URL = "http://192.168.1.20:8765/mcp"


async def read_recent_health(window_s: int = 30) -> dict[str, dict]:
    async with streamable_http_client(MCP_URL) as streams:
        read_stream, write_stream, _session_id = streams
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            listed = await session.list_tools()
            available = {tool.name for tool in listed.tools}
            required = {
                "health.get_heart_rate",
                "health.get_hrv",
                "health.get_imu_state",
            }
            missing = required - available
            if missing:
                raise RuntimeError(f"Health MCP tools missing: {sorted(missing)}")

            async def call(name: str) -> dict:
                result = await session.call_tool(name, {"window_s": window_s})
                payload = result.structuredContent
                if payload is None:
                    raise RuntimeError(f"{name} returned no structuredContent")
                return payload

            heart_rate, hrv, imu = await asyncio.gather(
                call("health.get_heart_rate"),
                call("health.get_hrv"),
                call("health.get_imu_state"),
            )
            return {"heart_rate": heart_rate, "hrv": hrv, "imu": imu}


print(asyncio.run(read_recent_health(30)))
```

SDK 返回的 `structuredContent` 是首选结果。兼容文本内容
`content[0].text` 是同一个 JSON envelope 的紧凑序列化版本。

## 3. 通用入参与返回 envelope

三个工具使用同一种可选入参：

```json
{"window_s": 30}
```

- `window_s`：整数，范围 `10..300` 秒。
- 省略或传 `{}` 时默认 `30` 秒。
- 不允许额外字段。

所有工具返回：

```json
{
  "ok": true,
  "data": {},
  "meta": {
    "schema_version": "0.3.0",
    "generated_at": "2026-07-26T01:30:00.000Z",
    "wearer_id": "xwen",
    "window_s": 30,
    "data_source": "live",
    "test_mode": false,
    "source_instance_id": "ef132c67-a98f-474a-a673-4ab6ea784790",
    "latest_observed_at": "2026-07-26T01:29:59.500Z",
    "age_ms": 500,
    "trace_id": "2ecf9675-aa90-4f70-8f39-89f867c7b40d"
  },
  "error": null
}
```

Agent 必须区分两种状态：

1. `ok == false`：工具调用失败，读取 `error` 并按 `retryable` 处理。
2. `ok == true` 且 `data.valid == false`：调用成功，但窗口内数据不足或质量
   不合格。不要把 `null` 当作零，也不要做医学推断。

## 4. `health.get_heart_rate`

调用：

```json
{"name":"health.get_heart_rate","arguments":{"window_s":30}}
```

`data` 字段：

| 字段 | 含义 |
|---|---|
| `valid` | 当前窗口是否可用于 Agent 判断 |
| `latest_bpm` | 最新有效心率，范围 20..240；不可用时为 `null` |
| `mean_bpm` / `min_bpm` / `max_bpm` | 窗口统计值 |
| `valid_sample_count` / `total_sample_count` | 有效样本数和总派生样本数 |
| `coverage_ratio` | 窗口覆盖率，范围 0..1 |
| `signal_quality_mean` | 平均信号质量，范围 0..1 或 `null` |
| `lead_off_seen` | 窗口内是否观察到导联脱落 |
| `clipping_seen` | 窗口内是否观察到 ADC 严重削顶 |
| `series` | 最多每秒一个 `{observed_at, bpm}` 趋势点 |
| `unavailable_reason` | `lead_off`、`adc_clipping`、`insufficient_window` 或 `null` |

只有 `data.valid == true` 时，Agent 才应使用 BPM 做规则判断。

## 5. `health.get_hrv`

调用：

```json
{"name":"health.get_hrv","arguments":{"window_s":30}}
```

`data` 字段：

| 字段 | 含义 |
|---|---|
| `valid` | HRV 估计是否通过数量和质量门限 |
| `estimate_type` | 固定为 `ultra_short_time_domain` |
| `rmssd_ms` | RMSSD；本项目规则使用的主要 HRV 指标 |
| `sdnn_ms` | SDNN |
| `pnn50_percent` | pNN50 百分比 |
| `mean_nn_ms` | 平均 NN 间期 |
| `valid_nn_count` | 有效 NN 间期数量 |
| `signal_quality_mean` | 平均 ECG 信号质量 |
| `lead_off_seen` / `clipping_seen` | 数据质量标志 |
| `unavailable_reason` | `lead_off`、`adc_clipping`、`quality_below_threshold`、`insufficient_nn_intervals` 或 `null` |

30 秒 HRV 是超短时工程估计，不是医疗诊断结论。只有 `valid == true` 时才使用
`rmssd_ms`。

## 6. `health.get_imu_state`

调用：

```json
{"name":"health.get_imu_state","arguments":{"window_s":30}}
```

`data` 字段：

| 字段 | 含义 |
|---|---|
| `valid` | 窗口覆盖率和 IMU 状态是否足够可靠 |
| `motion_score` | 0..100，越高表示运动越剧烈 |
| `still_ratio_percent` | 窗口内静止时间百分比，0..100 |
| `level` | `still`、`light`、`moderate`、`vigorous` 或 `null` |
| `coverage_ratio` | 窗口数据覆盖率，0..1 |
| `imu_online` | IMU 是否在线，未知时为 `null` |
| `method` | 固定为 `mpu6050_accel_gyro_activity_v1` |
| `series` | 最多每秒一个 `{observed_at, score}` 趋势点 |
| `unavailable_reason` | `imu_offline`、`insufficient_window` 或 `null` |

## 7. 错误与重试

当 `ok == false` 时：

```json
{
  "ok": false,
  "data": null,
  "meta": {
    "schema_version": "0.3.0",
    "generated_at": "2026-07-26T01:30:00.000Z",
    "wearer_id": "xwen",
    "window_s": 30,
    "data_source": null,
    "test_mode": null,
    "source_instance_id": null,
    "latest_observed_at": null,
    "age_ms": null,
    "trace_id": "2ecf9675-aa90-4f70-8f39-89f867c7b40d"
  },
  "error": {
    "code": "RATE_LIMITED",
    "message": "Health MCP rate limit exceeded.",
    "retryable": true,
    "retry_after_ms": 60000,
    "details": {}
  }
}
```

| `error.code` | Agent 行为 |
|---|---|
| `INVALID_ARGUMENT` | 不重试；修正参数，尤其是 `window_s` |
| `RATE_LIMITED` | 等待 `retry_after_ms` 后重试 |
| `INTERNAL_ERROR` | 可按 `retry_after_ms` 有界重试，并记录 `meta.trace_id` |

进程级每分钟限制：心率 120 次、HRV 60 次、IMU 120 次。不要轮询得比数据更新
速度更快；通常一次事件调用三个工具即可。

## 8. Health Webhook 与 MCP 的组合策略

当 Agent 收到 Health Webhook，且：

```json
{
  "recommended_capabilities": ["health.inspect_recent_metrics"],
  "evidence": {"recommended_window_s": 30}
}
```

Agent 应执行：

```text
window = clamp(evidence.recommended_window_s, 10, 300)
并行调用 health.get_heart_rate({"window_s": window})
并行调用 health.get_hrv({"window_s": window})
并行调用 health.get_imu_state({"window_s": window})
```

随后：

- 先检查每个 envelope 的 `ok`。
- 再检查每个 `data.valid` 和 `unavailable_reason`。
- 使用 `meta.latest_observed_at`、`meta.age_ms` 和 `meta.data_source` 判断时效性。
- 将 Webhook 的最新触发快照与 MCP 的窗口统计一起交给 Agent 策略。
- MCP 结果只提供工程观测，不授权机器人动作，也不构成医疗诊断或急救判断。

## 9. 接入验收清单

1. CPE 能访问 `http://<windows-address>:8765/mcp`。
2. MCP `initialize` 成功。
3. `tools/list` 恰好看到上述三个业务工具。
4. 三个工具用 `{}` 和 `{"window_s":30}` 均能调用。
5. 客户端使用 `structuredContent`，并正确区分 `ok` 与 `data.valid`。
6. `window_s=9` 能被识别为 `INVALID_ARGUMENT`。
7. 无数据、IMU 离线或 ECG 质量不足时不会把 `null` 当作零。
8. Agent 不发送 Authorization Header，也不把 8765 暴露到公网。

冻结的机器可读合约是：

```text
docs/specs/health-mcp-v0.3.contract.json
```

若本文与合约发生冲突，以该 JSON Schema 和服务端 `tools/list` 返回为准。
