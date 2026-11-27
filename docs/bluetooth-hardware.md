# PHY6235SC 蓝牙硬件与写频链路

更新：2026-11-28。用户明确本机蓝牙芯片为 **PHY6235SC，BLE 5.4，RISC-V 内核**；来源见 [用户报告](evidence/owner-hardware-20261127.json)。本页分别记录芯片规格、官方应用协议证据和待定位的板级连接。本轮取得厂家原文手册并补齐 SC 的 SOP16 引脚参考；耳机音频结论及新恢复的 ADC/APRS 链见 [蓝牙音频可行性](bluetooth-audio-feasibility.md)。

## 1. 器件身份与家族规格

[奉加科技官方 PHY6235 产品页][phy6235]支持用户提供的 BLE 5.4 与 RISC-V 信息。该页列出的是 PHY6235 家族，不能据此确定完整 `PHY6235SC` 订货后缀的每项配置。

| 项目 | 官方家族参考 | 对本机的记录方式 |
| --- | --- | --- |
| 处理器 | 32 位 RISC-V，最高 48 MHz，支持 JTAG | 架构与用户报告一致；未测本机时钟或 JTAG 接线 |
| 蓝牙 | BLE 5.4，1 / 2 Mbps PHY | 协议版本与用户报告一致；未测本机协商速率 |
| 存储 | 8 KB SRAM、80 KB ROM、256 / 512 KB Flash | 新取得手册确认 SC-W02C/W02I 为 256 KB、SC-W04I 为 512 KB；本机完整订货后缀待核对 |
| 主机接口 | UART、SPI Master、USB 2.0 | 不据此指定电台使用哪个接口 |
| 封装 | 家族为 SSOP24 / SOP16 | 厂家订单表列出的 PHY6235SC 为 SOP16；以下给出器件参考，不等于已测 PCB 走线 |
| 时钟参考 | 最小应用提及 16 MHz 晶振；RTC 列有内部 32 kHz RC | 不把用户报告的 32.768 kHz 晶振自动归到蓝牙 |

官方页提供 **PHY6235 BLE SoC Datasheet v1.0，2024-09-26** 的[下载入口][phy6235-ds]，该链接此前和本轮读取均返回 403。2026-11-28 从[代理商公开下载入口][phy6235-mirror]取得了带 Phyplus 版权的厂家原文 **PHY6235 Product Specification v1.0**：修订表为 2024.5，文件元数据为 2024-08-11，40 个 PDF 物理页面，正文标页 1–35。它与官网文件名为 20240926 的版本分别记录，未宣称二者逐字相同。

该 PDF 大小 2,290,068 字节，SHA-256 为 `3fce0054ed8fd72981ec0dc39cb762333ce34d8796a703e367b2da1d52c7cb70`。本轮已提取正文，并渲染目视核对 SOP16 表及订单表。证据元数据见 [bluetooth-audio-hardware.json](evidence/bluetooth-audio-hardware.json)；仓库不包含厂商手册全文。

[官方开发工具页][phy-sdk]目前将 SDK 3.x.x 明确配给 PHY6222/PHY6252、2.x.x 配给 PHY6212。本次未找到并核实适用于 PHY6235 的公开 SDK，不将上述其他型号 SDK 作为 PHY6235SC 的实现依据。

### 1.1 PHY6235SC 的器件参考引脚

以下来自上述厂家手册正文 p4–5 的 SOP16 表，以及 p22 的 ADC 通道表。**表格描述芯片自身，不代表已确认 UV-K10 Pro 的 UART、ADC、复位或音频走线。** UART 可经 IOMUX 分配，不能仅凭 GPIO 名称指认板上 TX/RX。

| SOP16 脚号 | 信号 | 与本轮分析相关的器件功能 |
| ---: | --- | --- |
| 1 | P08 | GPIO / 复用 |
| 2 | P00 | GPIO / 复用 / VPP |
| 3 | P02 | GPIO / 复用 |
| 4 | P03 | GPIO / 复用 |
| 5 | P04 | GPIO；ADC channel 1 |
| 6 | P05 | GPIO；ADC channel 7 |
| 7 | P06 | GPIO；ADC channel 2 |
| 8 | P07 / Vbat | 手册标注的 GPIO / 电源相关复用名；实际供电连接另核对 |
| 9 | P12 | GPIO；ADC channel 4 |
| 10 | P13 | GPIO；ADC channel 10 |
| 11 | P14 | GPIO；ADC channel 5 |
| 12 | XTAL16M_I | 16 MHz 晶振输入 |
| 13 | XTAL16M_O | 16 MHz 晶振输出 |
| 14 | VDD3 | 手册引脚表的 3.3 V 电源标注；不据此推断本机供电 |
| 15 | VSS | 地 |
| 16 | RF | 2.4 GHz 射频端 |

正文 p30 的订单表列出 `PHY6235SC-W02C`、`PHY6235SC-W02I`（256 KB Flash）及 `PHY6235SC-W04I`（512 KB Flash），均为 SOP16。用户当前提供的 `PHY6235SC` 尚未包含这些完整容量/温度后缀。

手册正文 p6 确认 CPU 为 N205 RISC-V，p7 确认总计 8 KB SRAM / 80 KB ROM，p20 标出 UART 最高 1 Mbps。它有通用 12 位 SAR ADC；首页 ADC 800 kHz 与 p22 manual mode 最高 320 kHz 的表述不同，不能把一个数值无条件用于所有模式。

I²S 仅在该手册 p28 的缩写词汇表出现，功能框图、外设章节和寄存器地址图没有建立 I²S 音频外设支持。没有找到 PHY6235 的 BR/EDR、A2DP、HFP、ISO/CIS/BIS 或 LC3 实现说明；这属于当前资料的支持边界，不是通过关键词缺失证明所有自定义音频都不可能。

## 2. 主控与蓝牙芯片分别分析

本仓库解码和仿真的 `K10PRO_ENG_v8.02.16` 是 PY32F071 主 MCU 路径，使用 ARM Thumb 指令。新增的 RISC-V 信息属于蓝牙 SoC，不改变已有主 MCU 识别、链接布局和驱动地址。没有取得 PHY6235SC 内部程序，现有 ARM 仿真也没有执行它的 RISC-V 固件。

| 对象 | 已有证据 | 尚未建立的关联 |
| --- | --- | --- |
| PY32 主 MCU | 官方主固件样本、USART1/USB/辅助 USART3 路径 | 哪条物理线路连接到 PHY6235SC |
| PHY6235SC | 用户提供的蓝牙型号；厂商 PHY6235 家族规格 | 本机程序版本、主机接口、引脚、供电/复位/唤醒、烧录入口 |
| 官方 Android K8 路径 | 型号绑定、GATT 服务与特征、发送策略及帧编码 | 真实芯片与主 MCU 之间如何转发、缓存和分包 |

辅助 USART3 在主固件中的地址为 `0x40004800`，可见 PB11 AF4 与另一函数配置的 PB10 AF4；相应 transport 2 路径使用明文和 X25。本轮确认 `0x08018B4E..0x08018B52` 以 `225 << 9 = 115200` 调用初始化函数 `0x0801A994`。这仍不足以证明 USART3 接到了 PHY6235SC。定位信息见 [引脚记录](pinmap.md)、[主固件写频协议](programming-protocol.md)及[音频分析](bluetooth-audio-feasibility.md)。

独立标识 **CS6150XC** 的功能仍未解决；用户已另行明确蓝牙器件为 PHY6235SC，不将 CS6150XC 归为蓝牙或擅改其完整型号。

## 3. 与现有 Android / Web 研究的对应

下表来自 Web 项目已恢复的官方 **QUANSHENG 1.1.0-64 / 1.1.0-68** APK 的 K8 引用链，不由 PHY6235 产品规格推导。固定引用版本为 [`8840949`][web-ble]；样本摘要与调用地址见 [AOT 证据][web-evidence]。

| 项目 | K8 / UV-K10 Pro 已恢复配置 |
| --- | --- |
| GATT service | `0000ff12-0000-1000-8000-00805f9b34fb` |
| 发送特征 | `0000fff2-0000-1000-8000-00805f9b34fb`，`withResponse` |
| 首选接收特征 | `0000fff3-0000-1000-8000-00805f9b34fb`，notify / indicate |
| 请求帧 | 明文，CRC-16/X25；Android K8 长度方言 |
| 回应帧 | X25 或 `FFFF` 占位；占位不标成 CRC 已验证 |
| 普通信道块写 | 12 字节头 + 128 字节数据槽，完整帧 152 字节 |
| K8 默认发送 | 整帧一次写入，帧后等待 120 ms |

型号绑定有直接调用证据：APK 64 的 `0xBD6F2C → 0xBD6F44`、APK 68 的 `0xBE82B8 → 0xBE82D0` 将 `K8RadioProtocol.gattProfile` 连接到 `BleGattProfile.k8`。这些是对应 `libapp.so` 的 ELF 代码地址，不是芯片寄存器或 GATT 句柄。

UUID 本身不能唯一识别机型；FFF1 在构造函数中出现但角色尚未确定。Web 的默认配置严格检查 FFF3 通知属性，官方应用另有通知特征回退。具体差异与版本限制沿用[现有 BLE 研究说明][web-ble]，不由芯片型号放宽到其他型号设备。

官方 APK 68 先请求 MTU 253，失败再请求 78，随后使用当前值；K8 超长帧走完整值长写。Web 实现把整帧交给浏览器，没有等价的 MTU/长写控制，也未取得真实设备长值写入验收。因此，家族的 1 / 2 Mbps 无线能力不能转换成已经验证的 Web 写入速度或成功率。详细实现见 [transports.mjs][web-transport]。

## 4. 后续定位的具体对象

1. 对照已恢复的主固件 UART 收发函数，寻找模块初始化、状态通知、透传切换和复位/唤醒的数据流；只在代码引用或走线证据成立后给 PHY6235SC 指定 MCU 引脚。
2. 用本页 SOP16 器件表对照实物，核对完整订货后缀、电源、主机口、晶振和调试口；记录蓝牙自身程序版本，不将主 MCU 更新包视为它的固件。
3. 用实机 GATT 服务发现和同一台设备的 MCU 侧报文核对转发关系，检查 152 字节写帧、回应时序及断连恢复。现有离线测试保留为软件证据。

本次补充建立了厂家手册层面的 SC 器件引脚参考，没有把它冒充新的板级 GPIO 映射，也没有实现或声称验证 PHY6235SC 芯片内固件。已恢复的官方应用读写逻辑继续由其精确 APK、CPS 和主固件证据支持。

[phy6235]: https://www.phyplusinc.com/product_detail/17.html
[phy6235-ds]: https://omo-oss-file.thefastfile.com/portal-saas/new2023112720123779343/cms/file/phy6235_ble_soc_datasheet_v1.0_20240926.pdf
[phy6235-mirror]: https://www.dnsj88.com/filedownload/2972840
[phy-sdk]: https://www.phyplusinc.com/support/4.html
[web-ble]: https://github.com/guanzi008/uv-k10pro-web/blob/8840949154fdb4766dac36fb132b0d0c8465c293/docs/ble-profile.md
[web-evidence]: https://github.com/guanzi008/uv-k10pro-web/blob/8840949154fdb4766dac36fb132b0d0c8465c293/docs/evidence/ble-aot-profile.json
[web-transport]: https://github.com/guanzi008/uv-k10pro-web/blob/8840949154fdb4766dac36fb132b0d0c8465c293/src/protocol/transports.mjs
