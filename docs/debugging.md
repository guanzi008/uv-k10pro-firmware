# RAM 诊断接口

## 使用前提

本文描述已经运行本工程固件的目标上的调试接口，不提供尚未核实的接线或首次刷写流程。需要已验证的调试连接与匹配的 `build/k10pro.elf` 符号。应用的 RAM 地址由链接器决定，通过符号 `k10_diag` 查找，不要把某次构建地址写死。

`k10_diag.magic == 0x4430314B` 表示主程序已经建立诊断结构，当前 `abi == 2`。`stage` 为 1/2/3/4 分别表示开始、Flash 探测、接收芯片探测、命令循环，新增 5 表示显示/输入初始化；启动顺序为 1→5→2→3→4。异常中断/HardFault 的状态另见 HAL 导出的 fault 符号。

`flags` 位 0 表示名义时钟可从当前配置解释，并不表示测量过实际频率；位 1 表示 JEDEC 读取成功；位 2 表示最近一次 Flash 数据读取成功；位 3 表示接收芯片版本响应可解析；位 4/5 表示显示/输入驱动就绪；位 6 表示已识别容量与预期 4 MiB 一致。**标志清除时，对应数据不能当成新有效结果。** 已识别 JEDEC 容量为零表示容量仍未知。接收芯片返回其他 PN 时也会保留原始版本字段，同时报告 `SI4732_UNEXPECTED_PART` 并禁止调谐。显示是只写接口，位 4 不是面板存在检测。

所有标量为 32 位无符号数，数据缓冲区为 64 字节。`struct_bytes` 允许外部工具检查 ABI 长度。字段的唯一权威定义是 [`diagnostics.h`](../firmware/include/k10/diagnostics.h)。

ABI 2 仅在 ABI 1 字段及 64 B buffer 之后追加字段，旧字段偏移不变。新增字段如下：

| 字段 | 含义 |
|---|---|
| `expected_flash_capacity_bytes` | 用户确认的 25Q32 密度，即 `0x400000`；不替代 JEDEC 识别 |
| `display_error` / `display_frames` | `display.h` 错误枚举与成功提交整帧次数 |
| `keypad_error` | `keypad.h` 错误枚举；默认 `KEYPAD_DISABLED=1` |
| `key_raw` / `key_stable` | 原始/去抖键码；`255` 表示无键，数字键 0 是有效键 |
| `ptt_raw` / `ptt_stable` | 独立 PC13 状态，1 为低有效按下；输入驱动未就绪时无效 |
| `key_last_events` | 最近一批事件位：1 矩阵按下、2 矩阵释放、4 PTT 按下、8 PTT 释放 |
| `key_last_released` | 该批矩阵释放事件的旧键码，无矩阵释放时为 255 |
| `key_event_count` | 非空事件批次计数，非单独按键动作总数 |

默认显示请求背光 (25,25,25)，每 250 ms 尝试更新一次状态页；SPI 失败后停止刷新，
驱动关闭背光并保留错误。键盘显式启用后，每 10 ms 尝试扫描；独立驱动要求 20 ms
稳定输入，未实现原厂长按/重复动作。两个周期都可能被其他阻塞命令延长。
PTT 和按键只更新诊断，没有连接到收发控制。

`K10_ENABLE_KEYPAD=1` 将 PA14 配为矩阵输出，接管 SWCLK；默认 0 完全不访问键盘 GPIO，
只保留继承的引脚状态。不要同时依赖该构建持续保有 PA14 调试时钟功能。

## 命令握手

仅支持一个调试端，且最多有一个未完成请求。暂停目标，先确认 `stage == 4` 且 `request_seq == completion_seq`，才可写参数及 `command`，最后写一个不同于 `completion_seq` 的新 `request_seq`。恢复运行；等待操作结束后再次暂停，确认 `completion_seq == request_seq`，再读取 `result` 与数据。上一条请求完成前不能修改参数、命令或请求序号，即使临时暂停了目标也不能覆盖未完成命令。

| command | 操作 | argument0 | argument1 |
|---:|---|---|---|
| 0 | 空操作 | 忽略 | 忽略 |
| 1 | 重新初始化 SPI2 并读取 JEDEC，识别成功后读取前 64 字节 | 忽略 | 忽略 |
| 2 | 读取外部 Flash | 起始字节地址 | 1..64 字节 |
| 3 | 重新探测 Si4732 | 忽略 | 忽略 |
| 4 | 请求 FM 接收调谐 | 6400..10800，单位 10 kHz | 忽略 |

示例：目标暂停时请求读取外部 Flash `0x1000` 起始的 32 字节：

```gdb
p k10_diag.stage
p k10_diag.request_seq == k10_diag.completion_seq
```

只有阶段为 4 且第二项为真时，才提交下一条请求：

```gdb
set var k10_diag.argument0 = 0x1000
set var k10_diag.argument1 = 32
set var k10_diag.command = 2
set var k10_diag.request_seq = k10_diag.request_seq + 1
continue
```

随后手动暂停，检查：

```gdb
p k10_diag.request_seq
p k10_diag.completion_seq
p k10_diag.result
p k10_diag.flash_error
p k10_diag.buffer_length
x/32bx &k10_diag.buffer
```

`result`：0 成功、1 未知命令、2 参数无效、3 驱动错误、4 接收芯片未就绪。驱动详细错误枚举见 `spi_nor.h` 与 `si4732.h`。Flash 读取失败时 `buffer_length` 为零，缓冲区中的旧字节不构成有效结果；调用方须按长度使用。

命令 4 会设置 Si4732 的接收频率并核对 FM_TUNE_STATUS 返回频率。它不控制主对讲 RF、喇叭功放或天线切换，因此成功也不能证明板上已经有声音。启动时不会自动调台。

## 还需要测量的事项

固件保留启动环境的时钟源并检测其配置；外部晶体频率未得到验证时，软件延时可能不准。芯片供电、RESET、外部上拉、watchdog 交接、调试口焊盘和 bootloader 恢复方式仍需逐项确认。若目标无法进入阶段 4，阶段与错误码可用于缩小范围，但当前没有任何实机成功记录。
