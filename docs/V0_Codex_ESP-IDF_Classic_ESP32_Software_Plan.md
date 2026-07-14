# AI 智能颈环 V0：经典 ESP32 + Codex + ESP-IDF 软件实施方案

> 版本：v0.2
> 日期：2026-07-14
> 状态：Hello World 已成功构建、烧录和运行，下一步交由 Codex 实现工程代码。

## 1. 已确认硬件

| 项目 | 当前事实 |
|---|---|
| 芯片系列 | 经典 ESP32，不是 ESP32-S3 |
| 模组 | ESP-WROOM-32 |
| CPU | 双核 |
| 芯片修订 | v3.0 |
| 无线 | Wi-Fi、经典蓝牙、BLE |
| 实际 Flash | 4 MB |
| 当前 Hello World 配置 | 2 MB |
| ESP-IDF Target | `esp32` |
| PSRAM | 暂不假设存在 |

新工程必须将 Flash 配置固定为 4 MB。不要继续继承 Hello World 的 2 MB 配置。

## 2. 与旧方案相比的关键修正

### 2.1 目标芯片

esp32

构建目标：

```powershell
idf.py set-target esp32
```

### 2.2 GPIO

经典 ESP32 的 GPIO4 属于 ADC2。ADC2 与 Wi-Fi 共用，虽然当前 V0 主要使用蓝牙，但没有必要给未来留下冲突。

新的固定引脚：

| 功能 | 引脚 |
|---|---|
| AD8232 OUTPUT | GPIO34 / ADC1_CH6 |
| AD8232 LO- | GPIO25 |
| AD8232 LO+ | GPIO26 |
| I2C SDA | GPIO21 |
| I2C SCL | GPIO22 |
| AD8232 SDN | 硬件拉到 3.3 V |

GPIO34 是输入专用脚，恰好适合模拟输入。

### 2.3 500 Hz 采样实现

旧方案写成了 `ADC Continuous DMA 500 Hz`，这不适合经典 ESP32。经典 ESP32 的连续 ADC 驱动面向高频采样，其硬件阈值远高于 500 Hz。

V0 使用：

```text
GPTimer 每 2 ms 触发
        ↓ ISR 只发送 task notification
高优先级采样任务被唤醒
        ↓
adc_oneshot_read(GPIO34 / ADC1_CH6)
        ↓
记录 sample_index + timestamp + raw ADC
        ↓
写入环形缓冲区
```

ISR 中不直接读 ADC、不分配内存、不发送蓝牙、不刷新 OLED。

验收重点不是“理论上每隔 2 ms”，而是：

- 实际时间戳间隔分布；
- 是否漏通知；
- 是否队列溢出；
- 500 Hz 长时间平均误差；
- 蓝牙发送时抖动是否增加。

如果这条路线的抖动仍不满足 RR 精度要求，再实验：

1. 1 kHz 采样后由电脑滤波和降采样；
2. 高速 ADC DMA + 明确的抽取实验；
3. 外部 ADC；
4. 更换后续硬件平台。

不要在没有测量结果时提前升级复杂度。

## 3. 无线传输选择

当前 ESP32 支持经典蓝牙，因此 V0 无线链路优先使用 Bluetooth Classic SPP：

```text
ESP32 SPP Server
    ↓ Windows 蓝牙配对
虚拟 COM 端口
    ↓ pyserial
电脑接收器
```

优点：

- 符合“蓝牙串口”的使用体验；
- Windows 端先复用串口接收器；
- 500 Hz 单通道 ECG 带宽极低；
- 调试比第一天就写 BLE GATT 更直接。

但协议层必须与传输层分离：

```text
packet encoder
├─ UART transport
├─ Bluetooth SPP transport
└─ BLE GATT transport（后续）
```

未来 ESP32-C6 不支持经典蓝牙，因此不能让业务代码直接依赖 SPP 回调。

## 4. Flash 和分区

`sdkconfig.defaults` 至少固定：

```text
CONFIG_IDF_TARGET="esp32"
CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y
CONFIG_PARTITION_TABLE_CUSTOM=y
CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions.csv"
```

当前分区建议：

```text
NVS       24 KB
PHY       4 KB
Factory   2 MB
Storage   剩余约 1.94 MB
```

V0 暂时不做 OTA 双分区。Bluetooth、GUI 协议和驱动逐渐增多后，默认 1 MB app 分区容易过紧，2 MB factory 分区更从容。

新工程生成后运行：

```powershell
.\tools\project.ps1 set-target
.\tools\project.ps1 reconfigure
.\tools\project.ps1 build
.\tools\project.ps1 size
```

构建日志必须确认 target 是 `esp32`，并确认 4 MB Flash 配置和自定义分区生效。

## 5. Codex 与 PowerShell 执行规则

### 5.1 不再依赖 Skill

上传的 `powershell-command-runner` 不是独立 Skill。它要求同级存在 `core/` 目录、执行契约、脚本和模式目录；只有 `SKILL.md` 时无法正常工作。

本项目不再要求安装或调用任何 PowerShell Skill。规则直接写入 AGENTS 文件：

```text
%USERPROFILE%\.codex\AGENTS.md   全局 Windows / PowerShell 执行契约
仓库根目录\AGENTS.md              ESP32 项目规则与必要的 PowerShell 兜底规则
```

### 5.2 全局规则

将本包的 `GLOBAL_AGENTS.md` 复制为：

```text
%USERPROFILE%\.codex\AGENTS.md
```

它负责：

- PowerShell shell 选择；
- Windows 路径和引用；
- 外部 CLI 与 `$LASTEXITCODE`；
- UTF-8 和非 ASCII 输出；
- `.ps1` 质量规则；
- 命令失败后的诊断与停止条件；
- 删除、覆盖、Git 重写和硬件擦除的审批边界。

不安装全局文件也不会阻塞本项目，因为仓库 `AGENTS.md` 已包含关键兜底规则。

### 5.3 仓库规则

仓库根目录 `AGENTS.md` 固定：

- 经典 ESP32 硬件事实；
- GPIO；
- 500 Hz GPTimer 架构；
- 原始 ECG 不滤波；
- SPP 传输顺序；
- PowerShell 执行与失败处理；
- 安全限制；
- Git 权限；
- 构建和测试要求。

全局 AGENTS 管“所有 Windows 项目怎样执行命令”，仓库 AGENTS 管“这个项目怎样被工程化”。不要把 GPIO34、AD8232 或人体安全规则放进全局文件。

## 6. 可选 ESP-IDF MCP

ESP-IDF v6.0.2 提供 `idf.py mcp-server`。Codex 支持在项目 `.codex/config.toml` 中配置 MCP。

本包提供：

```text
.codex/config.toml.example
```

验证 EIM 和当前 Codex 客户端支持后，复制成：

```text
.codex/config.toml
```

它不替代 AGENTS 中的执行规则：

- 全局和仓库 AGENTS 负责正确使用 Windows shell；
- ESP-IDF MCP 负责让 Agent 更结构化地调用 ESP-IDF 项目工具；
- `AGENTS.md` 负责项目规则和边界；
- `tools/project.ps1` 负责可重复的本地命令入口。

## 7. 仓库结构

```text
ai-smart-collar/
├─ AGENTS.md
├─ PLANS.md
├─ CONTRIBUTING.md
├─ .gitignore
├─ .editorconfig
├─ config/
│  ├─ local.example.ps1
│  └─ local.ps1                 # 不提交
├─ tools/
│  ├─ doctor.ps1
│  └─ project.ps1
├─ firmware/
│  ├─ CMakeLists.txt
│  ├─ sdkconfig.defaults
│  ├─ partitions.csv
│  ├─ main/
│  └─ components/
├─ pc_app/
│  ├─ pyproject.toml
│  ├─ src/
│  └─ tests/
├─ docs/
│  ├─ plans/
│  └─ protocol/
├─ .codex/
│  └─ config.toml.example
└─ .github/workflows/ci.yml
```

## 8. 固件阶段

### F0：工程骨架和自检

Codex 实现：

- ESP-IDF 原生 C 工程；
- target `esp32`；
- 4 MB Flash；
- 自定义分区；
- 统一 `board_config.h`；
- 启动打印芯片型号、revision、核心数、实际 Flash、配置 Flash、固件版本；
- 编译和 size 检查。

### F1：I2C 扫描

- SDA GPIO21；
- SCL GPIO22；
- 扫描 `0x08` 至 `0x77`；
- 预期 MPU6050 `0x68`；
- OLED 通常 `0x3C`；
- 不把扫描放进高频循环。

### F2：OLED 状态页

刷新不超过 2 Hz：

```text
ECG WAIT 500Hz
SPP -- IMU OK
```

运行后：

```text
ECG RUN 500Hz
SPP OK LEAD OK
```

不显示波形。

### F3：MPU6050

- 50 Hz；
- 原始加速度和陀螺仪；
- 与 ECG 共用单调时钟；
- I2C 失败计数；
- IMU 离线不能拖死 ECG。

### F4：ECG 采样

- ADC1_CH6 / GPIO34；
- ADC oneshot；
- GPTimer 2 ms；
- ISR 只通知任务；
- 高优先级任务读取 ADC；
- 环形缓冲；
- 原始 uint16；
- sample index；
- monotonic timestamp；
- LO-/LO+；
- clipping、late sample、overflow flags。

### F5：UART 二进制调试

在不接人体时验证：

- 包编码；
- CRC；
- 500 Hz 数据完整性；
- 电脑端解析；
- 连续运行。

### F6：Bluetooth Classic SPP

参考 ESP-IDF 的 SPP acceptor 思路，实现：

- 可发现设备名；
- Windows 配对；
- SPP 虚拟串口；
- 与 UART 共用 packet encoder；
- 发送批量 ECG 样本；
- 断线状态；
- transport queue overflow；
- 不在采样任务中直接调用阻塞发送。

### F7：BLE 预留

只定义 transport interface，不急着实现。BLE 是未来手机和 C6 路线，不阻塞当前 Windows V0。

## 9. 任务和核心建议

经典 ESP32 双核可先采用：

```text
Core 1:
- ECG sampling task，高优先级
- GPTimer notification handling

Core 0 / system:
- Bluetooth stack
- packet and transport task
- I2C / IMU task
- OLED task
```

是否固定核心必须通过运行数据验证，不把“绑核”当成神秘护符。采集任务绝不能打印每个样本。

建议任务：

```text
ecg_sample_task
imu_task
packet_task
transport_task
display_task
status_task
```

## 10. 数据协议

继续使用传输无关的二进制包：

```c
typedef struct {
    uint16_t magic;
    uint8_t protocol_version;
    uint8_t packet_type;
    uint16_t payload_length;
    uint32_t packet_sequence;
    uint64_t timestamp_us;
} packet_header_t;
```

ECG 包建议每包 20 点，即 40 ms：

```c
typedef struct {
    uint32_t first_sample_index;
    uint16_t sample_rate_hz;
    uint8_t sample_count;
    uint8_t flags;
    uint16_t samples[20];
} ecg_payload_t;
```

Flags 至少包括：

```text
LO_MINUS
LO_PLUS
ADC_CLIPPING
SAMPLE_LATE
SAMPLE_MISSED
SAMPLE_QUEUE_OVERFLOW
TRANSPORT_OVERFLOW
HISTORICAL_DATA
```

## 11. PC 程序

第一阶段使用统一串口接收器，同时支持：

- USB UART COM；
- Windows Bluetooth SPP COM。

Python 技术栈：

```text
pyserial
numpy
pandas
neurokit2
PySide6
pyqtgraph
pytest
```

顺序：

1. 协议和模拟数据；
2. 串口接收；
3. 原始文件保存；
4. 实时原始波形；
5. NeuroKit2 十秒滑动窗口；
6. R 峰、RR、HR、SQI；
7. GUI；
8. 结果写回设备的控制协议。

## 12. Git 版本管理

`main` 始终可构建。非简单修改使用：

```text
feat/<topic>
fix/<topic>
test/<topic>
docs/<topic>
chore/<topic>
```

提交遵循 Conventional Commits：

```text
feat(firmware): add GPTimer ECG sampler
fix(build): configure 4MB ESP-WROOM flash
```

Codex 可以修改、构建、测试，但默认不能：

- commit；
- push；
- tag；
- force push；
- reset hard；
- clean -fd；
- 擦除设备 Flash。

复杂任务在 `docs/plans/` 建 ExecPlan。协议、GPIO、分区和安全规则变化必须同步更新文档。

## 13. 第一轮 Codex 工作

第一轮不要直接冲向 ECG + SPP + GUI 全家桶。

执行顺序：

```text
工程骨架
→ 4MB Flash 与分区
→ 启动硬件信息
→ I2C 扫描
→ 协议 C/Python 数据结构和测试向量
→ build + size + pytest
```

使用 `docs/CODEX_START_PROMPT.md` 中的提示词。

## 14. 验收标准

第一轮完成：

- `idf.py` target 明确为 `esp32`；
- 构建日志与配置为 4 MB；
- 固件成功 build；
- size 可查看；
- I2C 扫描代码可编译；
- C/Python 协议测试一致；
- Git 工作区变化清楚；
- 没有自动烧录、提交或推送。

ECG 阶段完成：

- 500 Hz 平均采样率可验证；
- 采样间隔抖动有统计；
- 漏采和 overflow 不会静默；
- 10 分钟 sample index 单调；
- SPP 发送不阻塞采样；
- 原始数据可保存和回放；
- NeuroKit2 能在静止数据识别 R 峰。

## 15. 当前路线总结

```text
经典 ESP32 / ESP-WROOM-32
GPIO34 ADC1
GPTimer 2 ms + ADC oneshot task
原始 ECG 主链路不滤波
UART bench debug
Bluetooth Classic SPP 人体无线 V0
Windows pyserial
NeuroKit2 正式分析
Git + AGENTS.md + PLANS.md + CI 固化流程
```
