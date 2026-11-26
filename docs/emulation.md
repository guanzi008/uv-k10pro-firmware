# 原厂函数仿真：信道、写频、NOR 与射频总线

本轮使用 **Unicorn 2.1.4 的 Cortex-M0 指令模型**执行官方英文 `8.02.16` 中的实际 Thumb 指令。实验输入是人工构造的信道、串行帧和存储内容。它能检验反汇编得到的数据流、分支与字段解释，不能代替 PY32F071 整机、物理 NOR、USB 时序或射频测量。

参考样本、地址与摘要全部固定。执行前必须匹配解码应用 SHA-256：

```text
061759672cc69a1b00420ce6fae67b0756d3db111f8fa4329cadc940dcd17f17
```

仓库只分发独立编写的模型、实验输入、断言和结果，不分发原厂指令字节。对应官方下载及解包说明见 [reverse-engineering.md](reverse-engineering.md) 和 [container-format.md](container-format.md)。

## 1. 已经实际执行了哪些部分

| 实验 | 执行的原厂逻辑 | 明确替换/提供的边界 | 结果 |
|---|---|---|---|
| 数据初始化 | `0x08002AB8` 解压器、`0x0801F008` scatter 描述符 | SRAM 初始清零 | 1,220 字节 `.data`，7,274 条模型指令 |
| 信道 | 读取、完整/局部保存、逻辑删除、属性缓存、字段校验、菜单格式化、扫描列表 | 底层 NOR 读/擦/页编程为人工字节数组；显式设置选中信道和波段状态 | 见 [信道证据](evidence/channel-layout.json) |
| 写频协议 | CRC、收帧 parser、dispatcher、读写 handler、三个 sender | 人工 ring 和 session；发送捕获；外部依赖逐项列出 | 见 [协议证据](evidence/programming-protocol.json) |
| NOR 更新 | `0x08019694` RMW、内存函数、`0x080197B2` 分页循环 | 仅底层读/擦/页编程替换 | 8 组实验通过 |
| RF 三线接口 | 寄存器读/写、逐位循环、GPIO 方向切换、软件延时；32 位寄存器对写入 | 仅 GPIO MODER/IDR/BSRR/BRR 模型与给定输入位流 | 7 组实验通过 |

信道实验中的 NOR RMW 也实际执行。协议的异常输入实验在 RMW 入口只捕获参数；它与真实 NOR 更新实验的证据边界分开记录。协议报告另列完整的合成 NOR 写入再读回实验，不能把不同实验的 hook 范围混为一谈。

## 2. CPU 与内存模型

共享实现：[emulate_stock.py](../tools/emulate_stock.py)。

| 区域 | 模型范围/权限 | 用途 |
|---|---|---|
| MCU Flash | `0x08000000` 起 128 KiB，CPU 只读/执行 | 应用装入 `0x08002000`；其余填 `FF` |
| MCU SRAM | `0x20000000` 起 16 KiB，读/写，不执行 | 原厂全局数据、缓存和栈 |
| 人工输入区 | `0x21000000` 起 64 KiB，读/写，不执行 | 隔离实验输入输出；**不是设备上的额外 RAM** |
| 返回哨兵 | `0x01000000` 的模型页 | 识别被调用函数正常返回 |
| 普通 MMIO | 默认不映射 | 意外访问会失败，不默认返回 0 |
| RF 实验专用 MMIO | `0x50001400` 的四个 GPIO 寄存器 | 实现明确语义；同一映射页内的其它寄存器访问也拒绝 |

CPU 选 Cortex-M0 是为了执行此样本用到的 Armv6-M/Thumb 指令，并不声称复现 M0+ 微架构、流水线和 PY32 外设。每次函数调用有指令数和主机时间上限，未正常返回、未建模内存访问、写只读区域或执行人工输入区都会报错。指令计数不是目标机周期数，软件 delay 不转化为实测微秒。

原厂数据解压结果 SHA-256：

```text
64ac4a1654da0883255d522224c2eba6922d204917a5302de5d2f1d27894245d
```

解压后的时钟变量初值为 8 MHz；主程序随后另行设置 48 MHz。单独执行初始化不能据此把机器的工作频率改判为 8 MHz。NOR 缓存地址初值为 `0xFFFFFFFF`。所有实验另外设置的全局状态都在各脚本中可见，不视作实机开机默认值。

## 3. NOR 写入行为

实际函数：

```c
/* 根据调用约定命名，不是厂商源码声明。 */
stock_rmw(address, source_pointer, byte_count, mode); /* 0x08019694 */
```

几何结构与缓存：扇区 4,096 字节，页 256 字节，SRAM 缓存 `0x20000D70`，缓存扇区地址变量 `0x20000048`。`NorModel` 的 2 MiB 容量只是实验输入；它既不是 JEDEC 读数，也不能用来证明机器装了 2 MiB 或 4 MiB 芯片。

| 条件 | 原厂执行结果 | 对编辑工具的影响 |
|---|---|---|
| 输入与缓存内容完全相同 | 不擦、不编程；已有缓存时也不读 | 重复写同数据可直接结束 |
| 目标旧字节全为 `FF` | 只编程请求范围，按页拆分 | 两种 mode 均保留请求范围外的数据 |
| 目标变化且存在非 `FF` 旧字节，mode0 | 擦除扇区，修改缓存，完整重写 4 KiB | 保留前后邻居；即便变化只需 1→0，也会走擦除路径 |
| 同上，mode1 | 擦除扇区，只重写到本次请求末尾，并将缓存尾部填 `FF` | 扇区后部原数据丢弃；不能按无损覆盖理解 |
| 请求跨 4 KiB 边界 | 分扇区执行上述规则 | mode1 在第二扇区仍可能清掉后部 |
| 连续修改同一扇区 | 重用缓存 | 多个写操作不能假定每次重新读取 NOR |

普通信道保存调用 mode0。电脑写命令按**请求起始地址**选择 mode：低于 `0x50000` 用 0，否则用 1；这个分支不证明高区的全部业务格式，也不证明其每一次写入都会擦除。协议详情见 [programming-protocol.md](programming-protocol.md)。

[emulate_storage.py](../tools/emulate_storage.py) 的八组断言比较整个合成 NOR，覆盖页边界、相同数据、前后邻居、mode1 的两条路径、两种跨扇区行为以及缓存复用。每组结果记录低层操作与最终 SHA-256，见 [storage-emulation.json](evidence/storage-emulation.json)。

底层模型遵守编程只能把 1 改为 0、整扇区擦除置 `FF`、页编程不跨 256 字节的规则。擦除 hook 同时模拟被截获 wrapper 中已确认的缓存副作用；它不模拟 SPI 波形、busy 等待、写保护、掉电或磨损。

## 4. 主射频寄存器接口

本轮不只给 RF 函数加返回值 hook，而是执行其实际 GPIO 读写指令，并记录时钟边沿。模型初始把三根控制脚设为输出且输出锁存为高；真实读取函数会自行把数据脚切为输入，再切回输出。

| 信号 | 固件访问 | 按 PY32F071 命名 |
|---|---|---|
| SCK | `0x50001400` 的 bit5 | PF5 |
| SDATA，双向 | 同一端口 bit6，读取 IDR `+0x10` | PF6 |
| SCN / CS，低有效 | 同一端口 bit9 | PF9 |
| GPIO 模式 | MODER `+0`，每脚 2 位 | 输入 0，输出 1 |
| 置位 / 复位 | BSRR `+0x18` / BRR `+0x28` | 模型记录逻辑状态变化 |

端口名的依据是 [OpenPuya 的 PY32F071 CMSIS 头](https://github.com/OpenPuya/PY32F071_Firmware/blob/master/Drivers/CMSIS/Device/PY32F071/Include/py32f071xB.h)：`GPIOF_BASE = 0x50000000 + 0x1400`。这仍是固件导出的 MCU 信号，不是 PCB 连接器针脚图。

已执行的函数与观察：

- `0x08018944(reg, value16)`：发送 8 位寄存器号，再发送 16 位值，均高位在先。写寄存器 `0x38`、值 `0x1234` 的输出位流为 `38 12 34`。
- `0x08018248(reg)`：命令字为 `reg | 0x80`，随后切 SDATA 输入并读取 16 位。例如读取 `0x67` 时发出 `E7`；人工提供 `A5 5A` 后函数返回 `0xA55A`。
- `0x08018588` / `0x08018530`：分别完成字节发送和 16 位接收；接收在 SCK 高电平时读取输入。
- `0x08018480(word32)`：低 16 位写寄存器 `0x38`，高 16 位写 `0x39`。人工输入 `0x12345678` 得到两个事务 `38 56 78`、`39 12 34`；这个值仅用于验证顺序，没有向无线电请求调谐。

这些结果与 BK48xx 风格三线寄存器接口相容。Beken 官方 BK4819 页面与厂商署名的 BK4829 数据手册提供接口级交叉线索；**共享接口不够区分 BK4819 与 BK4829，也不足以确认完整寄存器位定义**。来源及镜像可信度见 [hardware-research.md](hardware-research.md)。

代码：[emulate_rf_bus.py](../tools/emulate_rf_bus.py)；结果：[rf-bus-emulation.json](evidence/rf-bus-emulation.json)。本实验没有模拟 RF 芯片、接收灵敏度、频偏、功率或音频。没有测量线速、建立/保持时间或板级电压。

## 5. 自行复现

先按仓库说明取得并解码原厂样本，再安装固定版本的可选依赖：

```sh
python3 -m venv .venv-re
. .venv-re/bin/activate
python3 -m pip install -r requirements-re.txt

python3 tools/emulate_stock.py reference/stock.raw.bin
python3 tools/emulate_channels.py reference/stock.raw.bin > channel-results.json
python3 tools/emulate_programming.py reference/stock.raw.bin --output protocol-results.json
python3 tools/emulate_storage.py reference/stock.raw.bin --output storage-results.json
python3 tools/emulate_rf_bus.py reference/stock.raw.bin --output rf-results.json

K10_REFERENCE_RAW=reference/stock.raw.bin make test
```

仿真底座会拒绝 Python 的 `-O` 模式，因为部分原指令对照检查使用 Python `assert`，关闭它们会使验证失去意义。所有脚本只操作本地模型和文件，不连接无线电。

普通 CI 不下载原厂样本。它执行独立合成输入测试，包括仿真底座的边界测试；依赖原厂文件的项目明确跳过。本地有指定精确样本时才能重做相应指令实验。已有 JSON 是一次可审查的研究记录，不表示公共 CI 拥有厂商文件。

## 6. 结论边界

现在可以确认的是给定样本、给定前置状态、给定外设模型之下的代码行为。仍需实机或原始抓包确认：会话建立与 RTC 字段、状态 gate 的实际来源、USB/UART/蓝牙对应关系、完整备份与回读、物理 Flash 型号与保护、断电行为、RF 芯片丝印、GPIO 到 PCB 的走线，以及各销售版本的硬件差异。

后续新增实验应优先替换一个明确的未知边界并给出可核对结果；不能因为函数在 CPU 模型里返回，就把整个设备功能标为已实现或可刷入使用。
