# ESP32-C3 SuperMini 移植接线与核验

## 当前结论

固件已经支持经典 ESP32 和 ESP32-C3 两个独立构建目标，并已成功烧录到用户的 ESP32-C3 SuperMini。芯片、4 MB Flash 和原生 USB 已由烧录工具确认；下面的 C3 外设引脚仍是第一版候选映射，尚未完成丝印、板载负载和传感器台架核验，因此还不是最终量产接线表。

## 旧板与新板对照

| 外设信号 | 经典 ESP32 / ESP-WROOM-32 | ESP32-C3 SuperMini 候选 | 迁移操作 |
|---|---|---|---|
| AD8232 `OUTPUT` | GPIO34 / ADC1_CH6 | GPIO0 / ADC1_CH0 | 从 34 改接 0 |
| AD8232 `LO-` | GPIO25 | GPIO3 | 从 25 改接 3 |
| AD8232 `LO+` | GPIO26 | GPIO10 | 从 26 改接 10 |
| I2C `SDA` | GPIO21 | GPIO6 | 从 21 改接 6 |
| I2C `SCL` | GPIO22 | GPIO7 | 从 22 改接 7 |
| AD8232 `SDN` | 3.3 V | 3.3 V | 不变，V0 不由 MCU 控制 |
| AD8232 / MPU6050 / OLED 电源 | 3.3 V、GND | 3V3、GND | 共地；GPIO 禁止接 5 V |
| 无线链路 | Bluetooth Classic SPP | BLE GATT | PC 端改选 BLE |

对应固件定义集中在 [board_esp32_classic.h](../../firmware/main/board_esp32_classic.h) 和 [board_esp32c3_supermini.h](../../firmware/main/board_esp32c3_supermini.h)，业务代码不再散落硬编码 GPIO。

## C3 接线顺序

断电后按以下顺序接线：

1. 所有模块 GND 接 C3 `GND`，模块供电接 `3V3`。
2. AD8232 `OUTPUT` 接 C3 `GPIO0`。
3. AD8232 `LO-` 接 C3 `GPIO3`，`LO+` 接 C3 `GPIO10`。
4. AD8232 `SDN` 继续接 3.3 V。
5. MPU6050 与 OLED 共用 I2C：两者 `SDA` 接 `GPIO6`，`SCL` 接 `GPIO7`。
6. 上电前用万用表确认 3V3 与 GND 没有短路，并确认没有把 5 V 接到任何 GPIO。

不要把丝印 `0` 误当成物理排针序号；这里的数字全部是芯片 GPIO 编号。

## 为什么选这些脚

- GPIO0 属于 ESP32-C3 ADC1，避免 GPIO5 的 ADC2；ECG 主链继续使用 ADC1，避免无线运行时的 ADC2 限制。
- GPIO2、GPIO8、GPIO9 是启动绑带脚，不分配给本项目外设。很多 SuperMini 的 GPIO8 还连接板载 LED，GPIO9 连接 BOOT。
- GPIO18、GPIO19 保留给原生 USB Serial/JTAG，不被应用占用。
- GPIO6、GPIO7 可通过 GPIO Matrix 用作 I2C。
- GPIO1、GPIO4 暂留作 ADC1 备选；只有实板证明 GPIO0 存在板载负载或噪声问题后，才重新评审并修改集中式板型配置。

## 怎么查手中的 SuperMini

### 1. 先查外观和资料

断电拍摄正反面高清照片，记录芯片或模组字样、USB 芯片、BOOT/RESET、板载 LED 和全部排针丝印。保存购买页或卖家原理图。`ESP32-C3 SuperMini` 是第三方通用商品名，不同克隆板的 LED、USB 和稳压器接法可能不同。

### 2. 查 Windows 枚举

插拔一次开发板，在设备管理器中记录新增的 COM/USB 设备、VID/PID 和驱动名称。原生 USB Serial/JTAG 与 CH340/CP210x 等外置 USB 转串口不能混为一谈。

当前这台电脑在 2026-07-23 识别到的 ESP32-C3 SuperMini 台架端口是 `COM21`，并已写入忽略提交的 `config/local.ps1`。COM 号可能随 USB 插口、驱动或设备变化，烧录前仍应核对一次。

### 3. 只读查芯片和 Flash

在明确端口后，可运行以下只读命令；它们不烧录、不擦除 Flash：

```powershell
. 'C:\Espressif\tools\Microsoft.v6.0.2.PowerShell_profile.ps1'
esptool.py --chip esp32c3 -p COM21 chip_id
esptool.py --chip esp32c3 -p COM21 flash_id
```

必须记录芯片确为 ESP32-C3、MAC、Flash 厂商和实际容量。只有实测 Flash 为 4 MB，才能直接复用当前 4 MB 分区配置。

### 4. 断电查连通性

只在断电状态使用万用表通断档，核对 GND、3V3、5V、板载 LED、BOOT 和 USB 相关走线。卖家资料与实板冲突时，以实板测量结果为准。不要在上电时使用通断档，也不要用表笔同时跨过相邻细间距焊盘。

### 5. 获得烧录许可后再做台架自检

首次烧录只连接开发板和无人体电极的外设台架。启动日志应报告 ESP32-C3；I2C 扫描预期 MPU6050 为 `0x68`，OLED 常见为 `0x3C`，但必须记录实际地址。LO-/LO+ 用安全的 10 kΩ 上拉/下拉逐路验证；ADC 用 0 V、已知中点电压和不超过 3.3 V 的台架信号验证单调性。

## 构建与 PC 连接

```powershell
. 'C:\Espressif\tools\Microsoft.v6.0.2.PowerShell_profile.ps1'
.\tools\project.ps1 doctor -Target esp32c3
.\tools\project.ps1 build -Target esp32c3
.\tools\project.ps1 size -Target esp32c3
```

PC GUI 中选择 `BLE`，扫描项目服务 UUID 后连接。经典 ESP32 仍可选择串口，通过 Windows 的 SPP COM 口工作。仓库目前仍默认 `esp32`；只有完成实板、传感器和 BLE 连续流验收后才切换默认目标。

## 2026-07-23 烧录记录

- 端口：`COM21`
- 烧录目标：`esp32c3`
- 芯片：ESP32-C3 AZ，QFN32，revision v1.1
- 能力：Wi-Fi、Bluetooth 5 LE、单核 160 MHz
- Flash：内置 XMC 4 MB
- USB：USB Serial/JTAG
- 应用镜像：541,312 字节，写入地址 `0x10000`
- bootloader、分区表和应用镜像：写入后哈希校验全部通过
- 复位：烧录结束后通过 RTS 硬复位
- 串口 monitor：未运行，因此尚未确认应用启动日志、I2C 地址、ADC、LO 状态或 BLE 广播

## 当前验证状态

| 项目 | 状态 |
|---|---|
| ESP32-C3 编译与尺寸检查 | 已通过 |
| 经典 ESP32 回归编译与尺寸检查 | 已通过 |
| PC BLE 字节流/分段解析测试 | 已通过自动化测试 |
| 芯片型号、Flash、USB | 已由 COM21 烧录握手确认 |
| 克隆板丝印和板载 GPIO 负载 | 未核验 |
| GPIO0/3/6/7/10 实板可用性 | 未核验 |
| ADC、LO、I2C、500 Hz 台架运行 | 未核验 |
| Windows BLE 10 分钟连续流 | 未核验 |
| 人体连接 | 未执行，也不得用 USB 台架执行 |

## 安全红线

USB 只用于不接人体电极的电子台架调试。人体 ECG 采集必须使用独立电池供电和 BLE 无线传输；电极接在人身上时，不得同时连接桌面 USB、墙充、正在充电的移动电源或接地台式仪器。
