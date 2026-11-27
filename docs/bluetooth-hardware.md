# PHY6235SC 蓝牙硬件与写频链路

更新：2026-11-27。用户明确本机蓝牙芯片为 **PHY6235SC，BLE 5.4，RISC-V 内核**。这补足了目标设备的蓝牙器件身份；来源见 [用户报告](evidence/owner-hardware-20261127.json)。本页分别记录芯片家族规格、官方应用协议证据和待定位的板级连接。

## 1. 器件身份与家族规格

[奉加科技官方 PHY6235 产品页][phy6235]支持用户提供的 BLE 5.4 与 RISC-V 信息。该页列出的是 PHY6235 家族，不能据此确定完整 `PHY6235SC` 订货后缀的每项配置。

| 项目 | 官方家族参考 | 对本机的记录方式 |
| --- | --- | --- |
| 处理器 | 32 位 RISC-V，最高 48 MHz，支持 JTAG | 架构与用户报告一致；未测本机时钟或 JTAG 接线 |
| 蓝牙 | BLE 5.4，1 / 2 Mbps PHY | 协议版本与用户报告一致；未测本机协商速率 |
| 存储 | 8 KB 保持型 SRAM、80 KB ROM、256 / 512 KB Flash | Flash 是家族选项，SC 对应容量待核对 |
| 主机接口 | UART、SPI Master、USB 2.0 | 不据此指定电台使用哪个接口 |
| 封装 | SSOP24 / SOP16 | 不据后缀猜封装或引脚号 |
| 时钟参考 | 最小应用提及 16 MHz 晶振；RTC 列有内部 32 kHz RC | 不把用户报告的 32.768 kHz 晶振自动归到蓝牙 |

官方页提供 **PHY6235 BLE SoC Datasheet v1.0，2024-09-26** 的[下载入口][phy6235-ds]，但本次读取 PDF 返回 403，未取得正文，因此没有宣称已核对 SC 订货表、引脚表、寄存器或烧录协议。

[官方开发工具页][phy-sdk]目前将 SDK 3.x.x 明确配给 PHY6222/PHY6252、2.x.x 配给 PHY6212。本次未找到并核实适用于 PHY6235 的公开 SDK，不将上述其他型号 SDK 作为 PHY6235SC 的实现依据。

## 2. 主控与蓝牙芯片分别分析

本仓库解码和仿真的 `K10PRO_ENG_v8.02.16` 是 PY32F071 主 MCU 路径，使用 ARM Thumb 指令。新增的 RISC-V 信息属于蓝牙 SoC，不改变已有主 MCU 识别、链接布局和驱动地址。没有取得 PHY6235SC 内部程序，现有 ARM 仿真也没有执行它的 RISC-V 固件。

| 对象 | 已有证据 | 尚未建立的关联 |
| --- | --- | --- |
| PY32 主 MCU | 官方主固件样本、USART1/USB/辅助 USART3 路径 | 哪条物理线路连接到 PHY6235SC |
| PHY6235SC | 用户提供的蓝牙型号；厂商 PHY6235 家族规格 | 本机程序版本、主机接口、引脚、供电/复位/唤醒、烧录入口 |
| 官方 Android K8 路径 | 型号绑定、GATT 服务与特征、发送策略及帧编码 | 真实芯片与主 MCU 之间如何转发、缓存和分包 |

辅助 USART3 在主固件中的地址为 `0x40004800`，可见 PB11 AF4 与另一函数配置的 PB10 AF4；相应 transport 2 路径使用明文和 X25。它与蓝牙应用协议具有可比较之处，仍不足以证明 USART3 接到了 PHY6235SC。定位信息见 [引脚记录](pinmap.md)及[主固件写频协议](programming-protocol.md)。

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
2. 以准确 SC 封装/订货资料核对电源、主机口、晶振和调试口；记录蓝牙自身程序版本，不将主 MCU 更新包视为它的固件。
3. 用实机 GATT 服务发现和同一台设备的 MCU 侧报文核对转发关系，检查 152 字节写帧、回应时序及断连恢复。现有离线测试保留为软件证据。

本次补充没有建立新的 GPIO 定义，也没有实现或声称验证 PHY6235SC 芯片内固件。已恢复的官方应用读写逻辑继续由其精确 APK、CPS 和主固件证据支持。

[phy6235]: https://www.phyplusinc.com/product_detail/17.html
[phy6235-ds]: https://omo-oss-file.thefastfile.com/portal-saas/new2023112720123779343/cms/file/phy6235_ble_soc_datasheet_v1.0_20240926.pdf
[phy-sdk]: https://www.phyplusinc.com/support/4.html
[web-ble]: https://github.com/guanzi008/uv-k10pro-web/blob/8840949154fdb4766dac36fb132b0d0c8465c293/docs/ble-profile.md
[web-evidence]: https://github.com/guanzi008/uv-k10pro-web/blob/8840949154fdb4766dac36fb132b0d0c8465c293/docs/evidence/ble-aot-profile.json
[web-transport]: https://github.com/guanzi008/uv-k10pro-web/blob/8840949154fdb4766dac36fb132b0d0c8465c293/src/protocol/transports.mjs
