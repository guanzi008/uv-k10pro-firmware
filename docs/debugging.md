# RAM 诊断接口

## 使用前提

本文描述已经运行本工程固件的目标上的调试接口，不提供尚未核实的接线或首次刷写流程。需要已验证的调试连接与匹配的 `build/k10pro.elf` 符号。应用的 RAM 地址由链接器决定，通过符号 `k10_diag` 查找，不要把某次构建地址写死。

`k10_diag.magic == 0x4430314B` 表示主程序已经建立 ABI 1 的诊断结构。`stage` 为 1/2/3/4 分别表示开始、Flash 探测、接收芯片探测、命令循环。异常中断/HardFault 的状态另见 HAL 导出的 fault 符号。

`flags` 位 0 表示名义时钟可从当前配置解释，并不表示测量过实际频率；位 1 表示 JEDEC 读取成功；位 2 表示最近一次 Flash 数据读取成功；位 3 表示接收芯片版本响应可解析。**标志清除时，对应数据不能当成新有效结果。** 已识别 JEDEC 容量为零表示容量仍未知。接收芯片返回其他 PN 时也会保留原始版本字段，同时报告 `SI4732_UNEXPECTED_PART` 并禁止调谐。

所有标量为 32 位无符号数，数据缓冲区为 64 字节。`struct_bytes` 允许外部工具检查 ABI 长度。字段的唯一权威定义是 [`diagnostics.h`](../firmware/include/k10/diagnostics.h)。

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
