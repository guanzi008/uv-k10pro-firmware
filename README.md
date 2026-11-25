# UV-K10 Pro 实验固件

[![Build experimental firmware](https://github.com/guanzi008/uv-k10pro-firmware/actions/workflows/ci.yml/badge.svg)](https://github.com/guanzi008/uv-k10pro-firmware/actions/workflows/ci.yml)

基于泉盛 **UV-K10 Pro 官方英文固件 8.02.16 的静态分析**，独立实现的第三方固件工程。当前版本 `0.1.0-exp` 面向开发者进行硬件初始化与诊断，目标平台为 PY32F071 兼容的 Cortex-M0+，采用 128 KiB Flash / 16 KiB SRAM 地址布局。

**当前没有实机启动、屏幕显示或收发验证记录，不能当作日常对讲固件使用。** 本仓库已经实现可构建的启动程序、硬件驱动、诊断应用及固件文件工具；完整屏幕/键盘界面、对讲接收与发射、GPS/APRS、USB 通信和恢复刷写流程仍待开发。软件通过编译和主机测试，不等于设备已验证。

## 已实现的内容

| 模块 | 当前实现 | 验证边界 |
|---|---|---|
| 启动与链接 | 48 项向量表、RAM 初始化、VTOR、弱中断处理、Flash/RAM 链接断言 | 主机检查和交叉编译；无实机启动证据 |
| MCU HAL | 解析继承的 RCC 时钟、SysTick、延时、128 位 UID、空闲等待 | 未测量实机时钟；未知时钟有状态标志 |
| 外部 SPI NOR | SPI2 模式 3、PA0/1/2 + PA3 CS、JEDEC 识别、带容量边界的读取 | 驱动未连接实机；不含写入/擦除 API |
| Si4732 | PB8/PB9 软件 I²C、POWER_UP、GET_REV、超时/NACK 处理、可选 FM 调谐与读回核对 | 需要芯片已供电并释放 RESET；音频线路待核对 |
| 诊断应用 | 启动探测、RAM 状态结构、GDB 命令邮箱、64 字节 Flash 读取 | 无显示屏或 USB/串口控制台 |
| 固件工具 | XOR128 + CRC16/XMODEM 封装、解包、检查、SHA-256 | 官方样本重封装逐字节一致；未验证官方刷写器接收新包 |
| 自动化 | Python 单元测试、驱动模拟、ARM 构建、向量/大小检查、开发构建产物 | GitHub Actions 不接触硬件 |

引脚、寄存器及固件内函数位置见 [引脚证据](docs/pinmap.md)，芯片判定和样本来源见 [逆向记录](docs/reverse-engineering.md)。这里的硬件型号是固件指纹与厂商资料匹配的结论，完整封装后缀仍需拆机确认。

首版本地验证结果、工具链、镜像大小、摘要和测试覆盖见 [验证记录](docs/validation.md)。

## 构建

需要 GNU Make、Python 3.10+、Arm GNU `arm-none-eabi` 工具链，以及用于驱动模拟测试的主机 C 编译器。Python 工具仅使用标准库。本地验证采用 Arm GNU Toolchain **14.3.Rel1 / GCC 14.3.1**；CI 使用 Ubuntu 提供的 GCC ARM Embedded。

```sh
git clone https://github.com/guanzi008/uv-k10pro-firmware.git
cd uv-k10pro-firmware
make test
make -j2 verify
make package
```

若工具链未在 PATH 中：

```sh
make CROSS_COMPILE=/absolute/path/to/bin/arm-none-eabi- package
```

`build/k10pro.elf` 保留调试符号；`build/k10pro.bin` 是原始应用镜像；`build/k10pro.experimental.bin` 是实验性更新容器。`build/verification.json` 和 `build/package.json` 记录结构检查与 SHA-256。这些命令只构建本地文件。

应用起点为 `0x08002000`。本工程把应用 Flash 上界保守设为 `0x0801F000`，保留前 8 KiB 地址范围与最后 4 KiB；RAM 为 `0x20000000..0x20003FFF`。**这是链接范围约束，不能保证一个尚未验证的刷写器如何擦除 Flash。** 没有提供一键刷入脚本。

## 诊断应用怎样使用

程序启动后读取 MCU UID、外部 Flash JEDEC ID；只有识别到明确支持的型号才读取 Flash 前 64 字节。随后尝试初始化 Si4732 并读出版本。缺少芯片、总线未响应或型号未知会记录错误码，应用仍进入诊断循环。

状态保存在 ELF 符号 `k10_diag` 对应的 RAM 中。它包含启动阶段、时钟可信标志、运行时间、原始 JEDEC ID、已识别容量、接收芯片版本、错误码及命令序号。这里没有虚构的串口波特率、USB VID/PID 或显示屏协议。

在**已建立的、经过核实的调试连接**上加载与当前程序匹配的 ELF 符号后，可暂停目标读取：

```gdb
p/x k10_diag
p k10_diag.stage
p/x k10_diag.jedec_id
p k10_diag.flash_capacity_bytes
x/64bx &k10_diag.buffer
```

支持重新探测、受限 Flash 读取和显式 FM 调谐命令。具体握手和字段说明见 [调试接口](docs/debugging.md)。仓库未确认 SWD 焊盘、板级 RESET/供电控制及 bootloader 恢复操作，文档不会把未核实的接线写成操作步骤。

## 分析自己下载的官方固件

从[厂商产品页](https://qsfj.com/products/11145)获取相应版本，保存在被忽略的 `reference/` 目录中：

```sh
python3 tools/fwtool.py inspect reference/K10PRO_ENG_v8.02.16_publish.bin
python3 tools/fwtool.py unpack reference/K10PRO_ENG_v8.02.16_publish.bin reference/stock.raw.bin
K10_REFERENCE_FIRMWARE=reference/K10PRO_ENG_v8.02.16_publish.bin make test
```

普通测试不依赖官方文件，参考样本测试在未提供路径时跳过。参考包的 SHA-256、解码后的 SHA-256、版本及 CRC 均有固定断言。容器格式见 [格式说明](docs/container-format.md)。官方文件、反汇编、外部 Flash 数据和 Si47xx 补丁均未随仓库发布。

## 接下来需要补齐的设备能力

1. 由具体硬件版本确认 MCU 丝印、外部 Flash 型号、SWD、供电/复位、晶振与 bootloader 交接状态；首先取得可恢复的实机启动记录。
2. 还原 LCD 控制器、初始化序列、背光和键盘矩阵，建立可以直接操作的本机界面。
3. 确认主对讲射频芯片、音频开关、校准数据结构，完成接收路径。
4. 在设备测量与校准资料齐全后开发发射控制；随后再处理 GPS/APRS、USB、设置保存和稳定更新流程。

详细可验证的里程碑见 [开发路线](docs/roadmap.md)。当前已知证据不足以把其他 UV-K1 / UV-K5 V3 工程直接当作 K10 Pro 固件。

## 许可证与来源

新编写的代码与文档使用 [MIT](LICENSE) 许可证。实现参考公开硬件文档与官方固件行为观察；这不是隔离团队的 clean-room 声明。第三方参考资料及商标不受本仓库 MIT 许可证授权，见 [NOTICE](NOTICE)。

## English summary

An independently written, experimental UV-K10 Pro firmware project targeting the PY32F071-compatible Cortex-M0+ application layout at `0x08002000`. It includes a freestanding runtime, read-only SPI NOR diagnostics, a Si4732 command driver, a debugger mailbox, a validated update-file codec, host tests, and CI. No vendor firmware or receiver patch is redistributed. Hardware boot, LCD/keypad, transceiver operation, and updater acceptance have **not** been validated; this is a developer bring-up project, not a complete replacement radio firmware.
