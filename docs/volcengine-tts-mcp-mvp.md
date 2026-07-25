# 火山引擎 TTS MCP MVP 接入说明

本文面向运行在同一台 Ubuntu 设备上的 A 进程。当前实现是轻量 JSON-RPC HTTP 适配器，只实现 `tools/call`，不实现完整 MCP 会话握手、`initialize` 或 `tools/list`。

## 接口

| 项目 | 值 |
|---|---|
| MCP URL | `http://127.0.0.1:9992/mcp` |
| 健康检查 | `GET http://127.0.0.1:9992/healthz` |
| 工具名 | `speak` |
| HTTP 超时建议 | `3s` |
| 服务范围 | 仅本机 loopback，不对局域网或公网开放 |

请求：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "speak",
    "arguments": {
      "text": "这是一条测试语音",
      "volume": 100,
      "speech_rate": 0
    }
  }
}
```

参数：

- `text`：必填，非空字符串，最多 2000 字符。
- `volume`：可选，整数 `0..100`，当前默认 `100`。
- `speech_rate`：可选，整数 `-50..100`，当前默认 `0`。
- 不接受额外字段；HTTP 请求体最大 64 KiB。

成功响应示例：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"status\":\"queued\",\"request_id\":\"...\",\"queue_depth\":1,\"volume\":100,\"speech_rate\":0}"
      }
    ],
    "isError": false
  }
}
```

A 进程应解析 `result.content[0].text` 中的 JSON。`status=queued` 只表示请求已进入播放队列，接口会立即返回，不表示云端合成或扬声器播放已经结束。

失败时 `result.isError=true`，错误信息同样位于 `content[0].text`。如果 HTTP 超时或连接中断，提交状态可能不确定，不建议立即自动重试，以免重复播报。队列满时可稍后有限重试。

## 最小 Python 调用

```python
import json
from urllib.request import Request, urlopen

payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
        "name": "speak",
        "arguments": {"text": "你好", "volume": 100},
    },
}
body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
request = Request(
    "http://127.0.0.1:9992/mcp",
    data=body,
    headers={"Content-Type": "application/json; charset=utf-8"},
)
with urlopen(request, timeout=3) as response:
    print(response.read().decode("utf-8"))
```

设备上也已安装交互式测试脚本：

```bash
cd /home/sunrise/tts-mcp
./send_tts.py
./send_tts.py "单次播报内容"
```

## 延迟与调用策略

- 本机实测入队响应：约 `10.8 ms`。
- 一条短语音的云端合成加播放实测：约 `3.4s`，在后台执行。
- A 进程不要等待播放结束，也不要轮询健康接口判断某条语音是否完成。
- 连续播报会按队列顺序播放，当前队列容量为 8。

## 开机自启动

服务：`volcengine-tts-mcp.service`。

当前配置包括：

- systemd 用户服务已 `enabled`；
- 用户 `sunrise` 已启用 lingering，无需登录即可在开机后启动用户服务；
- 服务启动前自动恢复 ES8326 输出路由和 100% 音量；
- 服务异常退出时自动重启。

运维命令：

```bash
systemctl --user status volcengine-tts-mcp.service
systemctl --user restart volcengine-tts-mcp.service
journalctl --user -u volcengine-tts-mcp.service -n 100 --no-pager
curl -fsS http://127.0.0.1:9992/healthz
```

## 环境变量

配置文件位于 `/home/sunrise/tts-mcp/.env`。A 进程无需读取其中的凭据。

- `VOLCENGINE_APPID`：火山引擎应用 ID。
- `VOLCENGINE_TOKEN`：该应用的 Access Token。
- `VOLCENGINE_RESOURCE_ID`：当前为 `seed-tts-2.0`。
- `VOLCENGINE_TTS_VOICE`：已开通额度的音色 ID。
- `VOLCENGINE_TTS_VOLUME`：默认播放音量，当前为 `100`。

不得把 `.env`、Access Token 或其他密钥提交到 Git 或写入 A 进程日志。
