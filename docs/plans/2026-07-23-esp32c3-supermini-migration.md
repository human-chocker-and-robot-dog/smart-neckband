# ESP32-C3 SuperMini 移植计划

## Goal

把当前基于经典 ESP32 / ESP-WROOM-32 的智能颈环固件与 Windows PC 数据链路迁移到 ESP32-C3 SuperMini，同时满足以下结果：

- ECG 仍以 500 Hz 采集未经数字滤波的 ADC 原始计数；
- MPU6050 仍以 50 Hz 采集，OLED 刷新不超过 2 Hz；
- V0 二进制包的序列号、采样索引、时间戳、状态标志和 CRC 语义保持不变；
- 用 BLE GATT 取代 C3 不支持的 Bluetooth Classic SPP；
- 保留经典 ESP32 的可构建配置，便于对照、回归和回退；
- 形成可核验的旧板/新板接线表，而不是仅凭“ESP32-C3 SuperMini”商品名假定克隆板完全一致。

本计划是实施时持续更新的 ExecPlan。软件迁移、自动化验证、COM21 烧录和 BLE 自动配对/短时数据闭环已经完成；外设引脚台架与 10 分钟连续流验收尚未完成。

## Initial state

- 当前分支基线来自 `feat/v0-foundation`，计划文档在独立 `docs/esp32c3-supermini-migration-plan` 分支编写。
- `firmware/sdkconfig.defaults` 固定 `CONFIG_IDF_TARGET="esp32"`、4 MB Flash 和 Classic BT SPP。
- `tools/project.ps1` 的 `set-target` 固定执行 `idf.py set-target esp32`；`config/local.example.ps1` 也固定期望 `esp32`。
- `firmware/main/board_config.h` 当前固定以下经典 ESP32 引脚：
  - ECG：GPIO34 / ADC1_CH6；
  - AD8232 LO-：GPIO25；
  - AD8232 LO+：GPIO26；
  - I2C SDA：GPIO21；
  - I2C SCL：GPIO22。
- `packet_task.c`、`oled_status.c` 和 `app_main.c` 直接依赖 `spp_transport.h`，尚未形成真正的通用传输接口。
- PC 端 `serial_io.py` 只读取 pyserial/Windows COM 口，并把 BLE 之前的无线链路视为 SPP 虚拟串口。
- 最大 V0 包是 ECG 包，共 68 字节；默认 ATT MTU 23 时一次通知只有 20 字节应用载荷，不能假设每个 V0 包总能放进一个 BLE notification。
- ESP32-C3 是单核 RISC-V 芯片，支持 Wi-Fi 和 BLE，不支持 Bluetooth Classic。官方资料还确认：GPIO0–4 为 ADC1 通道，GPIO5 为 ADC2；C3 的 ADC2 oneshot 结果不稳定且默认不支持；GPIO2、8、9 是启动绑带脚；GPIO18/19 默认用于 USB Serial/JTAG。
- “ESP32-C3 SuperMini”不是单一的乐鑫官方开发板型号，市场上存在板载 LED、USB 接法和丝印不同的克隆版本。最终接线必须以用户手中实板、购买页或原理图、芯片识别结果为准。

## Scope

### Included

- 实板身份、Flash 容量、USB 接口和可用 GPIO 的非破坏性确认流程；
- 经典 ESP32 与 ESP32-C3 SuperMini 的引脚对照和新接线文档；
- ESP-IDF target、构建脚本、板级配置、日志和文档迁移；
- ECG ADC、AD8232 lead-off、I2C、GPTimer、任务优先级在 C3 上的适配；
- 固件传输抽象和 ESP32-C3 NimBLE GATT 外设实现；
- Windows PC 端 BLE 接收、分片重组、复用现有 PacketParser/记录/分析/GUI；
- C/Python golden vectors、固件构建、PC 测试和台架验收；
- 在实现完成后更新 `AGENTS.md`、README、协议和硬件接线文档，使 C3 成为默认目标，同时保留经典 ESP32 回退配置。

### Excluded

- 修改 V0 原始 ECG 数据语义、在固件中滤波或做医疗诊断；
- 未经用户明确许可烧录、擦除 Flash、打开串口 monitor 或连接人体电极；2026-07-23 用户已单独授权并完成一次 C3 烧录，但未授权 monitor 或人体连接；
- 首轮移植中加入 Wi-Fi 传输、OTA、BLE Audio 或手机应用；
- 未确认实板 Flash 容量前改变分区布局；
- 未取得电池供电无线实测日志前声称人体连接已验证。

## Design decisions

### 1. 建议引脚映射

以下是第一版建议映射。它避开 GPIO2/8/9 启动绑带脚、GPIO18/19 原生 USB、GPIO5 的 ADC2，并保留 GPIO1/4 作为 ADC1 备选。只有通过“板型确认与引脚核验”门禁后才写入最终板级头文件。

| 功能 | 经典 ESP32 / ESP-WROOM-32 | ESP32-C3 SuperMini 建议 | 选择理由与核验 |
|---|---|---|---|
| AD8232 OUTPUT / ECG | GPIO34 / ADC1_CH6 | GPIO0 / ADC1_CH0 | 使用 C3 的 ADC1；GPIO0 不是 C3 启动绑带脚。用已知 0–3.3 V 台架信号确认通道和量程。 |
| AD8232 LO- | GPIO25 | GPIO3 | 普通数字输入，避开启动绑带、USB 和板载 LED。用 10 kΩ 上拉/下拉逐一验证标志。 |
| AD8232 LO+ | GPIO26 | GPIO10 | 普通数字输入，避开启动绑带、USB 和板载 LED。用 10 kΩ 上拉/下拉逐一验证标志。 |
| I2C SDA | GPIO21 | GPIO6 | C3 GPIO Matrix 可映射 I2C；与 SCL 成对布线并通过扫描确认。 |
| I2C SCL | GPIO22 | GPIO7 | C3 GPIO Matrix 可映射 I2C；扫描应报告实际 OLED/IMU 地址。 |
| AD8232 SDN | 硬件接 3.3 V | 仍接 3.3 V | V0 不由 MCU 控制。 |
| 传感器电源 | 3.3 V / GND | 3V3 / GND | 逻辑和模拟输入均按 3.3 V 域处理；禁止把 5 V 接到 GPIO。 |

新板暂不分配的关键脚：

- GPIO1、GPIO4：保留为 ADC1 备选，若实板证明 GPIO0 受板载电路影响，可在台架复测后换用；
- GPIO2、GPIO8、GPIO9：启动绑带脚，其中 GPIO8 常接板载 LED、GPIO9 常接 BOOT 按钮；
- GPIO5：ADC2_CH0，C3 的 ADC2 oneshot 不作为 ECG 方案；
- GPIO18、GPIO19：原生 USB D-/D+，不能被应用重配；
- GPIO20、GPIO21：优先保留给 UART0/恢复调试；
- GPIO10 以外的剩余安全脚：作为后续扩展，最终以具体 SuperMini 原理图为准。

外部 JTAG 的传统信号可映射到部分 GPIO4–7，但本方案使用 C3 的 USB Serial/JTAG；固件初始化 I2C 后 GPIO6/7 归 I2C 使用。若实板调试必须启用外部 JTAG，则重新分配 I2C，不在同一配置中抢占引脚。

### 2. 板型确认与“怎么查”

执行迁移前建立一张实板证据表，并把照片/购买链接/原理图来源记录到硬件文档：

1. 断电观察正反面丝印、芯片/模组标记、USB 芯片和板载 LED/BOOT/RESET 连接；确认它确实是 ESP32-C3，而不是外形相近的 S2/S3/C6 SuperMini。
2. 在 Windows 设备管理器或 PowerShell PnP 信息中记录插拔前后新增的 COM/USB 设备、VID/PID 和接口类型；区分原生 USB Serial/JTAG 与外置 CH340/CP210x。
3. 仅做只读识别：用 ESP-IDF v6.0.2 环境运行 `esptool.py --chip esp32c3 -p <PORT> chip_id` 与 `flash_id`，记录芯片型号、MAC、Flash 厂商和实测容量。该步骤不烧录也不擦除。
4. 将实板丝印与具体卖家原理图、ESP32-C3 数据手册逐脚交叉核对。克隆板资料冲突时，以实板连通性和测试结果为准。
5. 断电后用万用表通断档检查 GND、3V3、5V 和可见走线；不要在上电状态使用通断档，不要直接探测可能短接的相邻焊盘。
6. 在明确获得烧录许可后，烧入最小板级自检固件：报告 target/单核/Flash，扫描 I2C，逐个验证 LO 输入，最后用已知台架电压验证 ADC1_CH0。此时不得连接人体电极。

确认门禁：芯片必须识别为 ESP32-C3；Flash 必须实测为 4 MB 才能复用当前分区表；GPIO0/3/6/7/10 必须在该克隆板上实际引出且没有不可接受的板载负载。

### 3. 双板板级配置，而不是散落条件编译

- 把采样率、缓冲区和协议常量保留为公共配置。
- 新增两个集中式板型配置：经典 ESP32 和 ESP32-C3 SuperMini；由构建 target/profile 选择，业务源文件不得硬编码 GPIO。
- `sdkconfig.defaults` 拆为公共 defaults 与 target 专属 defaults；在 Gate A–D 完成前默认 profile 保持 `esp32`，同时可显式构建 `esp32c3` profile。
- `tools/project.ps1` 的 `set-target` 不再写死 `esp32`，而是从明确的板型/target 参数或本地配置读取；doctor 同时检查 target、Flash、工具链和串口配置。
- 切换 target 会重建生成目录和 sdkconfig；实施时先确认没有用户手工配置需要保留，生成文件不提交。

### 4. 保持采样架构，针对单核 C3 重新测量

- 继续使用 GPTimer 每 2 ms 在 ISR 中只发 task notification，继续在高优先级任务中调用 `adc_oneshot_read()`。
- ECG 主链仍只保存和发送原始 ADC 计数，lead-off、clipping、missed notification 和 overflow 必须保留。
- 不把 C3 改为 ADC continuous/DMA 500 Hz。
- C3 为单核，BLE host/controller、I2C/OLED 和采样任务会共享 CPU；初始优先级沿用现值，但以 10 分钟统计结果决定是否调整。任何调优都不能让传输回调阻塞采样。
- ADC clipping 上限不直接假定为 4095；先用 C3 的实际默认位宽和台架读数确认，再把边界集中到板级/ADC 配置中。

### 5. 用通用 transport 接口隔离 SPP 与 BLE

- 新建 transport 中性接口：`start`、`enqueue`、`get_status`，以及 connected、congested/backpressured、queue usage、queue overflow、drop、write error 等中性状态。
- 经典 ESP32 profile 继续编译 SPP 后端；C3 profile 只编译 BLE 后端，不让 C3 看到 `esp_spp_*` 或 Classic BT Kconfig。
- `packet_task.c` 和 `oled_status.c` 只依赖中性接口。
- 为保持 V0 wire compatibility，当前状态包中历史命名为 `spp_*` 的字段先保持字节布局和偏移不变，文档明确其在 C3 profile 下代表“活动传输队列”；若要重命名字段，另开协议版本，不在本移植中悄悄改线协议。

### 6. C3 使用 NimBLE GATT，V0 包字节不变

- 使用 ESP-NimBLE 实现单连接 GATT peripheral；NimBLE 相对节省单核 C3 的内存和代码体积。
- 定义一个项目专用 service，至少包含设备到 PC 的 notify characteristic；PC 到设备的 write characteristic 只为后续控制/HR/SQI 回写预留，首轮不扩展业务协议。
- 连接后协商较大 ATT MTU 和 Data Length Extension，但不能把 Windows 成功协商 MTU ≥ 71 当作唯一可用路径。
- 采用 ESP-IDF v6.0.2 自带的 `ble_uart` 公共组件。组件按协商后的 ATT 载荷自动切分完整的 V0 字节流，不再增加第二层应用分片头；PC 端把任意 notification chunk 直接送入增量 `PacketParser`，因此 V0 golden vectors 不变。
- notification 边界不等于 V0 包边界。解析器用 magic、长度和 CRC 从截断或损坏字节流恢复；连接与重连时清理旧 TX 队列，异常继续计入 transport counters。
- 用 notification 而不是 indication 传实时数据，以免确认链路阻塞采样；可靠性由包序号、CRC、分片检测和状态计数观察。

### 7. PC 端增加 BLE ByteSource，保留串口回退

- 抽取接收字节源接口，让 SerialPacketReader 和新的 BlePacketReader 复用 PacketParser、RawBinaryRecorder、buffers、publisher 和分析线程。
- Windows BLE 后端优先采用 Bleak；扫描用固定 service UUID/设备标识，不只按可变设备名匹配。
- GUI 明确显示连接类型（BLE 或 SPP/USB 串口）和设备地址/标识；MTU 由 BLE UART/操作系统协商，解析器不依赖 GUI 暴露 MTU。
- 单元测试用合成 notifications 覆盖完整包、20 字节分片、跨包粘连、丢片、乱序、重连和 CRC 错误，不依赖真实蓝牙硬件。

### 8. USB 只作无人体电极的台架调试

- SuperMini USB-C 若连接到 GPIO18/19，则使用 ESP32-C3 USB Serial/JTAG 做烧录和日志；应用不得占用 GPIO18/19。
- USB 连接时只允许电子台架验证，不接人体电极。
- 人体 ECG 只允许独立电池供电和 BLE 无线链路；不得同时连接桌面 USB、墙充、充电宝充电口或接地仪器。

## Work breakdown

1. **锁定实板身份和证据**：收集板子正反面照片、购买链接/原理图，运行只读芯片与 Flash 识别，记录 USB VID/PID、COM 口和实际 Flash；输出“已确认/未确认”板型清单。
2. **建立双 target 构建基线**：保存经典 ESP32 的可构建 profile，新增 C3 profile，更新 wrapper/doctor/local example；只构建最小启动与协议自检，确认 `CONFIG_IDF_TARGET=esp32c3`、单核和分区大小。
3. **落地板级引脚配置**：新增 C3 SuperMini 集中式板型头文件，按确认后的 GPIO 更新 ADC/lead-off/I2C；补充编译期校验，禁止使用 GPIO2/8/9、GPIO5 ADC2 和 GPIO18/19 USB。
4. **完成无无线的传感器台架构建**：先暂用 USB 日志/空 transport，验证 GPTimer、ADC oneshot、I2C 扫描、MPU6050、OLED 和状态计数能在 C3 构建运行，不把 BLE 问题混入采样排障。
5. **抽象 transport**：让 packet/OLED/app 依赖中性接口；经典 ESP32 SPP profile 回归构建并保持原行为。
6. **实现 C3 NimBLE GATT 后端**：加入 BLE UART 服务、通知队列、背压、ATT 自动切片、重连清理和统计；保留完整 V0 包字节。
7. **实现 PC BLE 接收**：加入 Bleak、设备发现、订阅和通用 ByteSource；notification chunks 直接进入增量解析器，GUI 可选择 BLE，同时保留 SPP/串口。
8. **自动化验证**：运行 C/Python golden vectors、C3 build/size、经典 ESP32 回归 build/size、PC tests；检查 diff 只包含计划范围文件。
9. **经许可的硬件验证**：在没有人体电极的台架上烧录 C3，执行有界 monitor，验证 I2C/ADC/LO/500 Hz/BLE；再做不少于 10 分钟的无线连续流测试。
10. **文档、默认目标和交付**：把确认后的接线表、板型照片来源、BLE UUID/分片格式、构建命令、Windows 配对/连接方法、安全限制和验收结果写入文档；C3 通过门禁后再改为默认目标。

## Validation

### 静态和构建验证

在 ESP-IDF v6.0.2 PowerShell profile 中执行，最终 wrapper 的 target 参数以实施结果为准：

```powershell
. 'C:\Espressif\tools\Microsoft.v6.0.2.PowerShell_profile.ps1'
.\tools\project.ps1 doctor -Target esp32c3
.\tools\project.ps1 build -Target esp32c3
.\tools\project.ps1 size -Target esp32c3
.\tools\project.ps1 build -Target esp32
.\tools\project.ps1 size -Target esp32
.\tools\project.ps1 pc-test
```

还必须完成：

- C3 build 日志明确显示 `esp32c3`，不得误报 ESP32-S3；
- 经典 ESP32 回归 profile 仍能 build/size；
- C 与 Python 的 V0 golden vectors 逐字节不变；
- BLE 字节流测试覆盖 20 字节 notification chunks、跨通知包和截断流恢复；
- 固件镜像适配实测 Flash 与分区，未确认 4 MB 时停止；
- `git diff --check`、`git status --short --branch` 和 diff 审查通过。

### 经用户明确许可后的硬件验证

- `chip_id`/`flash_id`：ESP32-C3、实际 Flash 容量与构建配置一致；
- 启动日志：target、单核、Flash、分区和板型引脚与文档一致；
- I2C 扫描：报告实际 MPU6050 地址（期望 0x68）与 OLED 地址（常见 0x3C）；
- LO-/LO+：分别拉高/拉低时只改变对应状态位；
- ADC：GND、已知中点电压和接近 3.3 V 的安全台架信号呈单调原始计数，不能超出输入电压范围；
- 500 Hz：10 分钟 sample index 单调，平均采样率、间隔分布、missed notifications、ring overflow 可量化；
- BLE：Windows 连接、断开、重连、20 字节分片回退和正常 MTU 路径均能工作；CRC、packet loss、transport overflow 有明确统计；
- 并发：BLE 持续发送、IMU 50 Hz、OLED ≤2 Hz 时不出现不可解释的 ECG 丢采样；
- 安全：以上 USB 台架测试不接人体电极。人体测试另行申请明确许可，并只使用独立电池 + BLE。

### Acceptance gates

- **Gate A — 板型可信**：芯片、Flash、USB、原理图/丝印和候选 GPIO 已确认；否则不最终接线。
- **Gate B — 软件可构建**：C3 与经典 ESP32 两个 profile 都通过 build/size，PC tests 通过。
- **Gate C — 传感器台架通过**：ADC、LO、I2C、500 Hz 统计通过，且没有人体电极。
- **Gate D — BLE 端到端通过**：Windows 连续接收 10 分钟，V0 CRC/序列/重连/分片统计满足验收。
- **Gate E — 默认目标切换**：只有 A–D 全部通过，才把文档和 wrapper 默认目标正式切为 C3。

## Risks and rollback

- **克隆板差异**：同名 SuperMini 可能换 LED、USB 或稳压器接法。回退为不接外设，只做照片/原理图/只读识别，绝不凭网图直接上电接线。
- **Flash 并非 4 MB**：若 `flash_id` 不符合预期，停止复用分区表，另行评审；不得直接向现有设备写入不同布局。
- **ADC 噪声和量程变化**：C3 ADC 的偏差、衰减和 RF 干扰可能与经典 ESP32 不同。保留原始计数，先台架测量，不用软件滤波掩盖硬件问题。
- **单核调度压力**：BLE/I2C/OLED 可能增加 2 ms 采样通知延迟。回退为关闭 OLED 和 BLE、使用空 transport 单独验证采样，再逐层恢复。
- **Windows BLE MTU/吞吐差异**：不能只依赖大 MTU。回退路径是 20 字节安全分片、降低非 ECG 状态发送频率或调优连接参数；不得降低 ECG 采样率或丢弃原始样本来掩盖吞吐不足。
- **传输重构回归 SPP**：保留经典 ESP32 profile 和 SPP 后端，每一步都回归构建；如果 BLE 未完成，C3 可以停在 USB 台架采样基线，不合入半工作的无线默认配置。
- **USB 安全**：USB 台架成功不等于人体安全。任何人体连接前必须断开 USB/充电/接地设备并改用独立电池无线模式。
- **回滚方式**：以小型 Conventional Commits 分阶段提交；经典 ESP32 profile 始终可构建。回滚使用正常 revert/切换 profile，不使用 `reset --hard`、`clean -fd`、强推或改写历史。

## Progress

- [x] 阅读仓库约束、当前 GPIO、采样、SPP、PC 串口和构建脚本基线。
- [x] 核对乐鑫官方 ESP32-C3 GPIO/ADC/USB/Bluetooth/target 文档。
- [x] 给出待实板核验的旧板到 C3 建议引脚映射。
- [x] 创建本 ExecPlan。
- [ ] Gate A：确认用户手中 SuperMini 的实板版本、Flash 和 USB。
- [x] Gate B：完成双 target 构建、传输抽象、PC 测试和 size 检查。
- [x] 经用户明确许可，将当前 C3 镜像通过 COM21 烧录并完成三段镜像哈希校验。
- [ ] Gate C：完成无人体电极的传感器台架验证。
- [ ] Gate D：完成 Windows BLE 端到端连续流验证。
- [ ] Gate E：把 ESP32-C3 SuperMini 设为仓库默认硬件目标。

## Discoveries

- 当前传输并未真正抽象：packet、OLED 和 app 启动路径都直接引用 SPP 类型和函数，C3 移植必须先切断这些依赖。
- 最大 V0 包为 68 字节，大于默认 BLE notification 的 20 字节 ATT 应用载荷；需要可靠的 transport 分片回退，而不能只写一个 GATT characteristic 就认为迁移完成。
- C3 的 GPIO5 虽是 ADC2_CH0，但 ESP-IDF v6.0.2 文档明确指出 C3 ADC2 oneshot 不稳定且默认不支持，因此 ECG 应留在 GPIO0–4 的 ADC1。
- 官方资料确认 C3 的启动绑带脚是 GPIO2、8、9，原生 USB 是 GPIO18/19；部分第三方 SuperMini 网页对“安全脚/JTAG/ADC2”的描述互相矛盾，不能作为唯一接线依据。
- 当前 PC 端解析、记录、分析和上传层已经以原始字节流为入口，新增 BLE ByteSource 后复用了大部分逻辑；V0 布局不变，只增强 PacketParser 的 CRC 失败后重同步策略。
- ESP-IDF v6.0.2 的 `ble_uart` 已按 ATT MTU 自动切片；新增应用分片头会重复造轮子并改变传输语义，因此实现采用裸 V0 字节流。为了从部分包后恢复，PacketParser 在 CRC 失败时只丢弃首字节并重新搜索 magic。
- 2026-07-23 软件验证通过：ESP32-C3 镜像 `0x84280` 字节，2 MiB 应用分区剩余 74%；经典 ESP32 镜像 `0xa0a50` 字节，剩余 69%；PC 测试 45 项全部通过。
- 2026-07-23 COM21 烧录握手确认实板为 ESP32-C3 AZ QFN32 revision v1.1、单核 160 MHz、内置 XMC 4 MB Flash、USB Serial/JTAG；bootloader、分区表和 541,312 字节应用镜像均写入并通过哈希校验。未运行 monitor，不能据此推断应用、I2C、ADC、LO 或 BLE 已正常运行。
- 首次 Windows 连接暴露了 DisplayOnly MITM 配对的可用性问题：动态六位码只打印到串口，用户无法从上位机完成配对。当前 bring-up 策略改为 Secure Connections Just Works + bonding，由 Bleak 发起首次配对；链路保持加密，但首次配对没有 MITM 身份校验，正式版计划在 OLED 上显示动态配对码后恢复认证配对。
- COM21 运行日志进一步确认 Just Works 达到 `encrypted=1 authenticated=0 bonded=1`。Windows 在 bonded-link 加密完成前可能先返回 GATT 服务，上位机因此只对 `Insufficient Authentication` 的 CCCD 写入做有界重试。日志还发现 100 Hz tick 下原 `pdMS_TO_TICKS(2)` 为零，单核 C3 的 packet task 会饿死 IDLE；改为 10 ms 后仍满足 40 ms 包周期。
- 实际上位机链路已从 `CollarC3-2E4A` 连续解析 849 个 V0 包（其中 415 个 ECG 包，最后样本索引 21099，无解析错误）。WinRT 在配对切换到 bonded GATT session 时会产生一次瞬时断开回调，即使通知随后持续；运行状态因此在收到真实 notification 时重新标记为已连接。
- Windows WinRT 对该设备使用 `services` 过滤并禁用服务缓存时会返回“设备不识别此命令”；上位机改为禁用缓存但枚举完整 GATT 服务。随后在 COM21 实板上完成闭环：发现 `CollarC3-2E4A`、自动复用/建立绑定、进入连接状态、解析 11 个 V0 包（6 个 ECG 包、CRC 错误 0）、主动断开，且设备重新广播。首次短测出现的序号缺口来自设备在连接前持续采样，不是 CRC 损坏。此结果不替代 10 分钟连续流验收。
- Windows 在新 Bond 建立后的首次重连还可能返回不完整的 GATT 缓存，表现为 TX characteristic 暂时不存在；Bleak 客户端现在在 WinRT 上禁用 cached services，并完整枚举 GATT 服务以兼容该设备。
- 2026-07-23 后续 GUI 复现表明连接仍不稳定：WinRT 能完成 GATT 枚举，但 TX CCCD 持续返回 `Insufficient Authentication`；清除 Windows Bond 后重新配对仍可复现，期间只会短暂收到 3–5 个 notification，不能视为有效连续连接。上位机已增加界面 DEBUG 面板和按次落盘的 Bleak/WinRT 日志，下一步需结合 COM21 NimBLE 安全事件日志修正设备端认证/CCCD 时序。
- GUI 日志随后暴露出另一条独立问题：直接由系统 Python 启动时，该环境有 PySide6 但没有 Bleak，扫描会在导入阶段失败。项目新增 `pc-gui` 入口，固定使用已安装完整依赖的 `pc_app\.venv`，并在界面和日志首行显示实际 Python 路径。
- Qt GUI 主线程属于 Windows STA；原同步 BLE 扫描用 `asyncio.run()` 阻塞了 GUI 消息泵，Bleak 因而报告 `Thread is configured for Windows GUI but callbacks are not working`。扫描现已移到独立后台线程，实测可从 GUI 路径发现 `CollarC3-2E4A`。继续连接后确认根因在设备权限配置：上游 `ble_uart` 的 encrypted 模式同时要求 TX CCCD `ENC + AUTHEN`，而当前 Just Works 配置只能达到 `encrypted=1 authenticated=0 bonded=1`，所以 Windows 虽显示已配对，CCCD 仍必然返回 `Insufficient Authentication`；固件权限修复与重新烧录尚未执行。

## References

- [ESP32-C3 Series Datasheet](https://documentation.espressif.com/esp32-c3_datasheet_en.pdf)
- [ESP-IDF v6.0.2: ESP32-C3 GPIO & RTC GPIO](https://docs.espressif.com/projects/esp-idf/en/stable/esp32c3/api-reference/peripherals/gpio.html)
- [ESP-IDF v6.0.2: ESP32-C3 ADC Oneshot](https://docs.espressif.com/projects/esp-idf/en/stable/esp32c3/api-reference/peripherals/adc/adc_oneshot.html)
- [ESP-IDF: ESP32-C3 Bluetooth LE overview](https://docs.espressif.com/projects/esp-idf/en/latest/esp32c3/api-guides/ble/overview.html)
- [ESP-IDF v6.0.2: ESP32-C3 USB Serial/JTAG Console](https://docs.espressif.com/projects/esp-idf/en/stable/esp32c3/api-guides/usb-serial-jtag-console.html)
- [ESP-IDF: Select the Target Chip](https://docs.espressif.com/projects/esp-idf/en/latest/esp32c3/api-guides/tools/idf-py.html)
- [ESP-IDF: BLE connection, MTU and data length](https://docs.espressif.com/projects/esp-idf/en/release-v5.4/esp32c3/api-guides/ble/get-started/ble-connection.html)
- [Third-party SuperMini pinout/schematic index — only for cross-checking the actual clone](https://www.sudo.is/docs/esphome/boards/esp32c3supermini/index.html)

## Result

软件迁移已实现：双 target/profile、集中式板型引脚、通用 transport、ESP32-C3 NimBLE GATT、PC Bleak 接收、GUI 选择和协议/接线文档均已落地，Gate B 已通过。当前镜像已成功烧录到 COM21 上的 ESP32-C3 SuperMini，并确认芯片、4 MB Flash 与原生 USB；有界串口日志和上位机短时闭环已验证 BLE 加密绑定、V0 数据接收、主动断开及恢复广播。外设 GPIO、ADC、LO、I2C 和 10 分钟连续流仍未验收。Gate A 仍缺克隆板/引脚证据，Gate C、D、E 也未通过，候选引脚表必须经台架核验后才能成为最终接线依据。
