# 2026-07-25 三份 Handoff 合并报告

## 1. 结论

三份交接文档对应的代码历史已经合并到本地 `main`：

- 麦克风、Hi ESP、BLE MIC1、PC 火山 ASR 与阈值 VAD；
- ordinary Agent Webhook、可靠指令持久化、回调接收与去重；
- Health SQLite 状态、四个只读 MCP tools、独立签名 Health Webhook；
- ESP32-C3 生产传感器固件、历史 Voice 固件和独立麦克风实验固件。

经典 ESP32、ESP-WROOM-32、Bluetooth Classic SPP、`esp32` 构建目标和
ESP32-C2 活跃配置均已从当前实现中移除。唯一支持的芯片目标是
`esp32c3`。

本次没有推送远端，也没有刷写硬件、打开串口监视器或进行人体采集。

## 2. 合并来源

| 来源 | 提交 | 最终归档 |
|---|---|---|
| 麦克风 Handoff | `e28feb7` | `docs/handoffs/microphone.md` |
| Health MCP Handoff | `e8656ac` | `docs/handoffs/health-mcp.md` |
| Webhook Handoff | `e44b377` | `docs/webhook/HANDOFF.md` |

关键集成提交：

- `0f0f797 feat(integration): unify C3 health and voice stack`
- `b63defb merge: integrate C3 health voice and webhook stack`

麦克风与 Health 分支共享 ordinary Agent Webhook 和可靠 Voice 基线，合并时
保留共同历史一次，没有复制相同功能。

## 3. 最终软件架构

### 3.1 生产传感器路径

```text
AD8232 ECG + MPU6050 + OLED
-> ESP32-C3 production firmware
-> encrypted BLE GATT V0 packets
-> PC main GUI
-> raw session recording and analysis
-> Health SQLite state
-> Health MCP stdio and optional signed Health Webhook
```

主 ECG 数据仍为未滤波 raw ADC counts。PC 在解析前记录原始 transport
chunk，Health 状态只读取已经提交的数据，不替换原始采集证据。

### 3.2 当前麦克风路径

```text
INMP441
-> ESP32-C3 WakeNet9s official Hi ESP
-> BLE MIC1 IMA-ADPCM
-> PC microphone GUI
-> PCM16 decode
-> Volcengine V3 streaming ASR
-> ambient-threshold VAD
-> durable ordinary Agent Webhook queue
-> Agent final reply callback
```

ASR final text 使用稳定的 `mic-*` instruction ID，先写 SQLite，再通知
dispatcher。重复 final 保持幂等，默认不保存 WAV，只有显式诊断选项才落盘。

### 3.3 Webhook 隔离

ordinary Agent Webhook 和 Health Webhook 保持独立：

| 项目 | ordinary Agent | Health |
|---|---|---|
| 入口 | `/v1/instructions` | `/v1/health-events` |
| 回调 | `/agent-replies` | 接收方收到事件后通过 MCP 查询 |
| 存储 | `webhook_client.sqlite3` | Health SQLite outbox |
| 身份 | instruction/reply IDs | wearer/event/notification IDs |
| 安全 | MVP trusted host/LAN | HMAC-SHA256 signed body |

两条队列、凭据、重试和去重语义没有混用。

## 4. ESP32-C3 迁移结果

- `tools/project.ps1` 的目标参数只接受 `esp32c3`。
- 生产板配置只保留 `board_esp32c3_supermini.h`。
- 删除 classic board header、SPP backend 和 `sdkconfig.defaults.esp32`。
- 生产 BLE 协议仍为 V0；麦克风实验使用独立 MIC1 framing。
- 当前引脚基线：ECG GPIO0、LO- GPIO3、LO+ GPIO10、I2C GPIO6/7、
  INMP441 BCLK GPIO4、WS GPIO5、SD GPIO20。
- 活跃源码和操作文档中没有 classic ESP32、ESP32-C2 或 SPP 引用。

## 5. 合并冲突与处理

主要语义冲突集中在以下位置：

- `pc_app/pyproject.toml`：合并 GUI、BLE、ASR、MCP 和测试依赖。
- `tools/project.ps1`：保留 PC、Health、Voice 和 C3 构建入口。
- `protocol.py`、`buffers.py`、`serial_io.py`、`ble_io.py`：保留 V0
  packet accounting、staged commit、source ownership 和重置语义。
- `gui.py`：保留原采集 UI，同时接入 Webhook 与 Health runtime 生命周期。
- `mic_capture_gui.py`：复用 ordinary Webhook tab，并加入 ASR final 的稳定持久化。
- 固件 CMake：保留生产传感器镜像和独立麦克风实验项目，避免未经硬件验证
  就把实时 ECG 与 WakeNet 音频任务强行放进同一个镜像。

Windows 构建审查还发现两个本地工程问题并已修复：

1. 默认 Ninja 高并发会让 GCC 子进程偶发无诊断退出。
2. `tools/inmp441-test.ps1` 直接使用损坏的全局 `idf.py` launcher。

两个构建脚本现在都支持 `-BuildJobs`，默认使用 4 个任务；INMP441 脚本会
优先调用 `IDF_PYTHON_ENV_PATH` 下的 Python 和 `IDF_PATH/tools/idf.py`。

## 6. 验证结果

### 6.1 PC 与 Health

| 验证 | 结果 |
|---|---|
| `pc-test` | 185 passed in 43.07s |
| 主 GUI offscreen 构造 | 通过 |
| 麦克风 GUI offscreen 构造 | 通过 |
| Python `compileall` | 通过 |
| PowerShell parser | 6 files 通过 |

此前完成的 30 分钟 synthetic Health soak：

| 指标 | 结果 |
|---|---:|
| duration | 1800.046s |
| state revisions | 3194 |
| MCP calls | 20271 |
| MCP exceptions | 0 |
| production outbox pending | 0 |

### 6.2 固件构建与容量

| 镜像 | `.bin` | size tool | DRAM | app 分区余量 |
|---|---:|---:|---:|---:|
| production ESP32-C3 | 544400 B | 544041 B | 37.74% | 74% of 2 MiB |
| historical Voice | 1461472 B | 1461103 B | 57.71% | 30% of 2 MiB |
| INMP441/Hi ESP | 624768 B | 624415 B | 30.68% | 60% of 1.5 MiB |

构建产物 SHA-256：

```text
D480429AE38136056EA6490786FAEDE1625F123EB77369F09E99B48F86B2018B  production smart_neckband.bin
1A84DB026C6CEAC524DD517E1A90F0ACEC53574290BACD884D2F24D7F9CB2DEF  historical Voice smart_neckband.bin
57390CE3172B0AED69D03B550291948A6CA883474CD593D0B73EC524EB8F9268  INMP441 inmp441_ble_capture.bin
BC56EFEEC122AE9FACDABDB9817BF2F224A8FA7585244B6E351DB84A8AA19088  INMP441 wn9s_hiesp srmodels.bin
```

INMP441 构建确认加载 `wn9s_hiesp`，模型约 122.83 KiB。

## 7. 本地运行 PC 应用

当前本地 `main` 位于：

```text
C:\Users\XWen1024\Documents\smart-neckband\data\worktrees\unified-integration
```

首次运行或切换 worktree 后，先重新绑定 editable install：

```powershell
Set-Location 'C:\Users\XWen1024\Documents\smart-neckband\data\worktrees\unified-integration'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\project.ps1 pc-setup
```

查看生产传感器、Health 和普通 Webhook 主界面：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\project.ps1 pc-gui
```

查看 Hi ESP、BLE 麦克风、ASR、VAD 和 Webhook 界面：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\project.ps1 pc-mic
```

Health runtime 需要在启动 GUI 前设置稳定的非姓名 wearer ID：

```powershell
$env:SMART_COLLAR_WEARER_ID = 'wearer-001'
$env:SMART_COLLAR_HEALTH_DB_PATH = "$PWD\data\health\health_state.db"
```

ordinary Webhook 功能还需要一个外部 Agent Webhook Gateway。PC 项目实现的是
持久化 client、dispatcher 和 reply receiver，不包含 Gateway 服务本身。

## 8. 本机构建命令

本机 Espressif installer venv 指向已经移除的 Python 3.12。当前可用的忽略
目录 venv 是 `data/idf-python-host-v6.0.2`。在这个 worktree 中执行：

```powershell
. 'C:\Espressif\tools\Microsoft.v6.0.2.PowerShell_profile.ps1'
$env:IDF_PYTHON_ENV_PATH = "$PWD\data\idf-python-host-v6.0.2"
$env:IDF_CCACHE_ENABLE = '0'

.\tools\project.ps1 build -Target esp32c3
.\tools\project.ps1 size -Target esp32c3
.\tools\project.ps1 build -Target esp32c3 -Voice
.\tools\project.ps1 size -Target esp32c3 -Voice
.\tools\inmp441-test.ps1 build
.\tools\inmp441-test.ps1 size
```

如果 Windows 编译器再次出现无诊断退出，可显式降低并发：

```powershell
.\tools\project.ps1 build -Target esp32c3 -BuildJobs 1
.\tools\inmp441-test.ps1 build -BuildJobs 1
```

## 9. 后续刷写步骤

刷写前确认 `config/local.ps1` 中端口正确。当前 C3 bench port 记录为 COM21。

生产传感器固件：

```powershell
.\tools\project.ps1 flash -Target esp32c3
```

当前推荐的 Hi ESP 麦克风固件：

```powershell
.\tools\inmp441-test.ps1 flash -Port COM21
```

两者是互斥镜像，刷入麦克风实验固件会替换生产 ECG/IMU 固件，反之亦然。
当前仓库包含两条完整可构建路径，但尚未把实时 ECG、IMU、OLED、WakeNet 和
MIC1 音频调度集成到同一个 ESP32-C3 镜像。

历史 `-Voice` 镜像不是当前推荐刷写路径。它生成的 `srmodels.bin` 只有 4 字节
占位，必须在获得精确许可和 SHA-256 匹配的自定义模型后，通过
`voice-model-provision` 单独写入 model 分区。

## 10. 安全边界

- USB 调试和刷写时不得连接人体电极。
- 人体 ECG 采集必须使用独立电池和无线传输。
- 电极连接人体时，不得同时连接桌面 USB、墙电、充电宝或接地仪器。
- 本次只验证构建和软件行为，没有验证真实 BLE 连接、麦克风声学效果、
  火山 ASR 在线服务、Agent Gateway 或人体数据。

## 11. 已知限制与后续建议

1. 单块 ESP32-C3 目前不能同时运行生产传感器与麦克风实验镜像。
2. 历史 Voice 自定义唤醒词模型没有随仓库交付，不能把占位 model 当成可用模型。
3. Espressif `esp-sr` 2.4.6 在 IDF 6.0.2 下输出多条 Kconfig bool warning，
   但三套构建均成功。
4. 当前 worktree 路径较长，CMake 对少数组件给出 object path length warning。
   若未来出现真实路径错误，应把工作树放到更短目录，而不是修改组件源码。
5. 本次没有进行硬件 flash/monitor 验证。下一步应先刷 INMP441 镜像验证 Hi ESP
   与 BLE 音频，再刷 production 镜像验证 ECG/IMU/OLED，不要连接人体电极。
