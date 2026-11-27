# UV-K10 Pro 电脑读写协议：固件调用链与离线验证

本页基于官方英文 **8.02.16** 样本，已经把接收帧、命令分发、Flash 读写处理、回应封装连成可重放的证据链。协议工具仅处理本地字节和文件，不打开 USB、串口、蓝牙或网络。

这里的“读信道/写信道”需要拆为两层：电脑先用 `0x02C3 / 0x02C5` 访问外部 Flash 的字节范围，信道记录和属性表再解释这些字节。普通信道的 64 字节记录、独立属性表及机器内部保存函数见 [channel-storage.md](channel-storage.md)。**`0x02DD` 不是已确认的写信道命令**；其下游包含射频数据发送路径，不能按命令编号相邻就把它当成信道持久化操作。

## 1. 分析对象和可复现程度

| 项目 | 内容 |
|---|---|
| 原厂下载与完整摘要 | [reverse-engineering.md](reverse-engineering.md) |
| 解码应用版本 | `8.02.16` |
| 应用基址 | `0x08002000` |
| 解码文件 SHA-256 | `061759672cc69a1b00420ce6fae67b0756d3db111f8fa4329cadc940dcd17f17` |
| 独立协议代码 | [programming_protocol.py](../tools/programming_protocol.py) |
| 原指令重放代码 | [emulate_programming.py](../tools/emulate_programming.py) |
| 共享 CPU 仿真底座 | [emulate_stock.py](../tools/emulate_stock.py) |
| 本次可复现结果 | [programming-protocol.json](evidence/programming-protocol.json) |
| 官方 CPS 静态交叉核对 | [cps-protocol-crosscheck.json](evidence/cps-protocol-crosscheck.json) / [analyze_cps.py](../tools/analyze_cps.py) |

证据分三类：

- **静态确认**：函数地址、寄存器参数、条件分支、USB 描述符。地址只适用于这个样本。
- **原指令重放**：Unicorn 执行样本中的真实 Thumb 指令，数据和外设边界由明确的 hook 提供。CRC、接收 parser、dispatcher、读写 handler 和发送封装都实际执行。
- **待实机确认**：接口接线、真实 USB 枚举、CPS 会话时序、蓝牙模块连接、Flash 芯片型号、断电行为和射频行为。本仓库没有这些实机验证记录。

重放器先执行样本 `.data` 解压初始化；BSS 为零。每个实验另行设置 session、状态字节、ring 内容和读写索引。它在主机端复现主循环“accepted 标志为真后才调用 dispatcher”的分支，没有模拟整个主循环、IRQ 或 DMA。MMIO 不映射；意外访问外设会停止实验。基础 42 项实验的 Flash 读返回人工序列 `(address + offset) & 0xff`，写调用只记录计划。另有独立的 6 次事务实验，将实际 RMW 接到合成 NOR 字节数组并完整读回；两者都没有真实硬件写入。

## 2. 实际存在的三条传输通道

共同发送入口是 `0x0800361C(r0=transport, r1=body, r2=body_length)`。

| transport | 发送函数 | 接收 ring | 固件内硬件证据 |
|---|---|---|---|
| `0` | `0x0801A8B0` | `0x20000564`，2060 字节 | USART1 基址 `0x40013800`；初始化 `0x0801A750`；PA9/PA10 AF1 |
| `1` | `0x0801ABC8` | `0x20001D9E`，384 字节 | 原生 USB；下游 `0x0801D7F8` 以端点 `0x81` 发送 |
| `2` | `0x0801AAF0` | `0x200022F8`，200 字节 | 辅助 UART 基址 `0x40004800`；初始化 `0x0801A994`，PB11 AF4；PB10 AF4 由 `0x0801AB38` 控制 |

按 PY32F071 外设映射，第三条 UART 对应 USART3；其真实连接到哪个板载模块仍需原理图或走线确认。厂商应用支持 K10 Pro 蓝牙写频这一事实，与“transport 2 就是蓝牙”不是同一级证据，不能直接互相替代。2026-11-27 用户明确蓝牙芯片为 PHY6235SC、BLE 5.4、RISC-V；它补足了器件身份，仍未证明 USART3/PB10/PB11 与该芯片的物理连接。已恢复的应用 GATT 配置与芯片规格的对应范围见 [bluetooth-hardware.md](bluetooth-hardware.md)，相关厂商资料见 [hardware-research.md](hardware-research.md)。

USART1 的模式选择函数 `0x0801A93C` 可向初始化函数传入 `38400` 或 `115200`。这些是代码中的配置值，没有测量物理串口。USB 通道有自己的 ring 和端点，不能因为它在电脑上表现为串口就把 USART1 波特率当作 USB 数据传输速率。

### USB 描述符已经定位

`0x0801CF24` 将位于 `0x0801DF94` 的描述符注册到 USB 栈。静态解析结果如下，已纳入证据 JSON 的 `static_usb_descriptors`，可由重放脚本重新提取。

| 字段 | 固件内值 |
|---|---|
| USB 版本 | `bcdUSB = 0x0200` |
| 设备类 / 子类 / 协议 | `0xEF / 0x02 / 0x01` |
| VID / PID | `0x36B7 / 0x0001` |
| 设备版本 | `bcdDevice = 0x0100` |
| EP0 最大包长 | 64 字节 |
| 配置描述符总长 | 75 字节 |
| 接口 0 | 通信类 `0x02`、ACM 子类 `0x02`、协议 `0x01` |
| 接口 1 | CDC 数据类 `0x0A` |
| 通知端点 | `0x83`，interrupt，最大包长 8，interval 字段 10 |
| 数据 OUT / IN | `0x02 / 0x81`，bulk，最大包长各 64 |
| 厂商字符串 | `QUANSHENG` |
| 产品字符串 | `K8 CPS` |
| 固件内序列字符串 | `2025080801` |

这直接支持固件实现 **USB CDC ACM 虚拟串口**；仍不是一份实机 `lsusb` 抓取。VID、产品字符串和固定序列字符串不能单独证明 MCU 厂商或某一台设备的唯一身份。`0x0801CEAA` 的类请求分支还处理 `0x20 / 0x21 / 0x22`，与 CDC 的 line-coding / control-line 请求相符。

## 3. 帧格式、CRC 和 XOR

所有多字节整数均为小端。外层帧为：

```text
AB CD | body_length:u16 | body[body_length] | checksum:u16 | DC BA
```

body 起始四字节为：

```text
command:u16 | declared_length:u16 | actual_payload[body_length - 4]
```

`body_length` 包含 command 和 declared_length，不包含外层两字节 checksum。**内层长度不能一律当作实际 payload 的总长。** 官方 CPS 0.56 写命令把内层值固定为 12，却发送 140 字节 actual_payload：12 字节固定请求头加 128 字节数据数组。hello 也保留内层值 4，同时序列化 24 字节 actual_payload。读请求及本页回应则与实际 payload 长度一致。

独立工具的 `Message` 分开保存 `declared_length` 与 `actual_payload_length`，并给出 `length_convention`。默认写请求跟随 CPS 0.56 的固定数组形态；`layout="compact"` 才选择本轮另行重放的紧凑格式。解析器接受已确认的 CPS 形态，仍拒绝 count 大于实际数据量，不把 padding 计入有效写入。下文的异常长度实验指特定的 inner=0 或缺失数据，不是把一切内外长度不相等都判为异常。

### 请求：CRC16/X25

原厂 CRC 函数 `0x08018B58` 使用反射多项式 `0x8408`、初值 `0xFFFF`、最终异或 `0xFFFF`，即 CRC-16/X25（也称 CRC-16/IBM-SDLC）。输入 ASCII `123456789`，实际执行返回 `0x906E`。

请求 CRC 在 **明文 body** 上计算，然后以小端存放。此 CRC 与固件升级容器中的 CRC16/XMODEM 不同；升级文件格式见 [container-format.md](container-format.md)。

### 回应：USB / USART1 的 `0xFFFF` 只是占位

| 方向 / 传输 | checksum 内容 | XOR 行为 |
|---|---|---|
| 三种传输的请求 | CRC16/X25(body) | transport 0/1 可用 XOR；transport 2 不用 |
| transport 0 / 1 的回应 | 固定 `0xFFFF` | 随全局模式可用 XOR |
| transport 2 的回应 | CRC16/X25(body) | 不用 XOR |

USB sender `0x0801ABC8` 和 USART1 sender `0x0801A8B0` 明确填入 `FF FF`。辅助 UART sender `0x0801AAF0` 则调用 CRC 函数。六组实际 sender 重放证据覆盖三种通道及两种全局 XOR 状态。

例如回应 body `c6 02 04 00 00 10 00 00`：

| 情况 | 实际完整回应 |
|---|---|
| USB / USART1，明文 | `abcd0800c602040000100000ffffdcba` |
| USB / USART1，XOR | `abcd0800d06e10e62e810d40decadcba` |
| 辅助 UART | `abcd0800c6020400001000005392dcba` |

因此工具把 USB / USART1 回应标成 `placeholder-unverified`，`crc_verified=false`。收到固定 `FFFF` 不提供 payload 完整性证明；它与请求 CRC 校验通过是不同结果。

### XOR16 是可选混淆

表地址 `0x0801E7AC`，16 字节循环：

```text
16 6C 14 E6 2E 91 0D 40 21 35 D5 40 13 03 E9 80
```

XOR 范围是 `body + checksum`，从 body 的第一个字节重新计数；外层魔数、长度和尾标不变。它与更新包 XOR128 是两套参数，不应混用，也不提供密码学认证。

transport 0/1 接收器查看线上首个命令半字：`0x02BC` 使状态 `0x200000C5` 变为 0，`0x6EAA` 使它变为 1；后者正好是 `0x02BC` 经本表 XOR 的结果。其他命令沿用已有状态。transport 2 跳过此检测及 XOR。状态更改发生在 CRC 判断之前：坏 CRC 的 hello 也可能改变这个软件状态，证据为 `hello_bad_crc`；这里只陈述状态机顺序，不推导硬件攻击结论。

## 4. 接收与分发调用链

原厂 parser 是 `0x0800E758`：

| 位置 | 参数 |
|---|---|
| `r0` | ring 地址 |
| `r1` | ring 容量 |
| `r2` | 当前读索引 |
| `r3` | 生产者索引 |
| 栈参数 0 | 解码输出地址 |
| 栈参数 1 | accepted 标志地址 |
| 栈参数 2 | transport |
| 返回 `r0` | 下一读索引 |

它扫描 `AB CD`，检查至少有 8 字节、`body_length + 8 <= ring_capacity`、整个帧已到达、尾部为 `DC BA`，再把 **body 和两字节 checksum 一起** 复制到输出，按状态去 XOR，计算请求 CRC。成功时才设置 accepted 字节为 1。

真实调用点是 `0x0800C642`、`0x0800C66A`、`0x0800C694` 附近的三段通道分支；统一解码输出在 `0x200004C4`。接受后调用 dispatcher `0x08003468(r0=transport)`，dispatcher 自己从这个固定输出地址读取 command。dispatcher 不接收外层长度参数。

下一已知缓冲区起始为 `0x20000564`，距离输出地址 160 字节；这只是两个地址的间隔，没有符号信息时不应把它写成已证明的 C 数组大小。本轮受控重放没有越过这段间隔：最长常规案例 write129 的 body+checksum 为 147 字节。

### 已验证的长度边界

| 实验 | 原指令结果 | 可以得出的结论 |
|---|---|---|
| 正确 CRC、完整请求 | 接受并分发 | 线上格式和调用链相符 |
| ring 从索引 380 跨尾部 | 接受，正确还原 body | USB ring 回绕逻辑有效 |
| CRC 错误 | 不分发 | CRC 在 dispatcher 前生效 |
| 外层帧未完整到达 | 不接受 | 不用尚未到达的尾部完成帧 |
| read count=128 | 执行读调用并回包 | 读 handler 接受 128 |
| read count=129 | parser 接受，handler 静默返回 | 128 限制在读 handler |
| write count=129，数据确有 129 字节 | parser→dispatcher→RMW hook | 写 handler 没有对应的 128 本地限制 |
| CPS 0.56：inner=12、body=144，count=64 / 128 | 接受；RMW 仅取 64 / 128 字节 | 固定 128 字节数组的 padding 被 count 限制，符合原厂主机形态 |
| 内层 declared_length=0，但外层 body 和 CRC 完整 | 仍进入 RMW hook | 已走完整链，inner=0 本身没有被拒绝 |
| count=16，帧内仅有 4 字节数据 | hook 看到 4 字节数据、2 字节 CRC、10 字节预置 `5A` | 缺少 count 与实有数据量的一致性检查，尾部来自人工预置缓冲区 |

最后两项使用人工 session、状态和预置 SRAM，RMW 被替换为记录 hook。它们不是实机可利用性验证，也没有产生真实写入、读出私人数据或实现利用代码。独立工具主动拒绝这些不一致请求。

## 5. 会话与 hello

`0x02BC` handler `0x08002D38` 把 body `+4` 的 u32 写到 `0x200000C0`，后续读写必须带回相同值。本页称它为 **session word**，没有把它等同于口令或可靠身份认证。

hello 还有必须注意的副作用：body `+8` 的 u32 被传给 `0x08019AC8`，下游写 RTC 计数寄存器。CPS `Link` 将该字段命名为 `utc` 并调用 `ConvertToUtcTimestamp`。`+0x0C` 为 1 时，另有字段复制到 RAM；CPS 元数据将它们命名为 APRS 固定经纬度、方向字节和高度，具体单位及有效范围仍需核对。CPS 普通 `Link` 的结构先清零，只显式填写 session 和 utc。离线 CLI 目前只提供读写请求草稿，没有自动 hello 或设置设备时间的动作。

hello reply 构造函数 `0x080035B8` 返回 command `0x02BD`、payload length `0x34`，body 总长 56。`+4` 起拷贝 16 字节版本区域，`+20/+21` 为两个状态字节，`+24` 起为 16 字节 challenge。版本字符串起始为 `8.02.16\0`；拷贝区域长度不意味着其余所有字节都为 NUL。

## 6. 外部 Flash 读命令 `0x02C3`

handler：`0x0800320C`。偏移相对 body 起始。

| 偏移 | 长度 | 请求字段 |
|---|---:|---|
| `+0` | 2 | command=`0x02C3` |
| `+2` | 2 | declared_length，CPS 请求为 12 |
| `+4` | 4 | 外部 Flash 起始地址 |
| `+8` | 2 | count |
| `+10` | 2 | 未使用/保留字段 |
| `+12` | 4 | session word |

检查顺序：session 相等；count 不大于 128；**起始地址**小于 `0x001FF000`。失败时不发送回应。固件局部代码允许 count=0；工具要求 1..128。

地址判断在 `0x0800323A`；它没有检查 `address + count`。例子 `address=0x1FEFFF, count=16` 确实到达读 hook。因此 `0x1FF000` 是这个 handler 的起点门限，不是容量检测结果，也不是已经证明的整段边界。

通过检查后，还有如下状态 gate：先取 `0x200001E0`；若值为 1，则改取 `0x200000BB`；最终值为 0 才调用 `0x080195E4(r0=address, r1=destination, r2=count)`。否则回应的数据区保持清零。这些状态字节的完整业务含义尚未定名。

回应：

| 偏移 | 长度 | 内容 |
|---|---:|---|
| `+0` | 2 | `0x02C4` |
| `+2` | 2 | `8 + count` |
| `+4` | 4 | 原请求地址 |
| `+8` | 2 | count |
| `+10` | 2 | 0 |
| `+12` | count | 数据；被 gate 挡住时为全 0 |

所以全零读回不必然表示 Flash 本来是零；需要结合会话和状态判断。

## 7. 外部 Flash 写命令 `0x02C5`

handler：`0x0800334C`。

| 偏移 | 长度 | 请求字段 |
|---|---:|---|
| `+0` | 2 | `0x02C5` |
| `+2` | 2 | CPS 0.56 固定为 `12`；紧凑实验格式为 `12 + count` |
| `+4` | 4 | 外部 Flash 起始地址 |
| `+8` | 2 | count |
| `+10` | 1 | CPS 命名 `u8AdvanceFlag`；已知在 `0x17000` 特殊状态下比较是否为 1 |
| `+11` | 1 | 保留字段 |
| `+12` | 4 | session word |
| `+16` | 128（CPS） | 固定数据数组，前 count 字节有效，剩余由 CPS 补零 |

CPS 写 body 因此固定为 **144 字节**，完整帧为 152 字节；字段 `u8DataLen` 虽然名称带 `u8`，.NET 类型签名实际是 `UInt16`。count=64 时只有前 64 字节进入 RMW，其余 64 字节不落入 Flash。`build_write()` 默认采用这一格式。显式紧凑实验格式省去 padding，以 `16 + count` 为 body 长度，两种形态都经过原固件指令重放。

session 不符或起点 `>=0x1FF000` 时静默返回。写 handler 没有读 handler 的 count<=128 检查，也没有末地址检查。工具仍将每块限制为 1..128，并要求**整个区间**低于 `0x1FF000`；这是独立工具的保守约束，不冒充原厂行为。

一般状态 gate 与读操作相同，另有两个精确起点特例：

- 起点 `0x18000`：`0x200000BB` 不为 0 时跳过写；允许时在写后调用 `0x0800F288` 重新加载该区域。
- 起点 `0x17000`：`0x2000008D` 恰为 1 时，要求请求 `+10` 字节为 1，否则跳过写。这个字段暂称 flag，不将它包装成口令解锁功能。

真正数据写入调用为：

```text
0x08019694(r0=address, r1=body+16, r2=count,
           r3=(address >= 0x50000 ? 1 : 0))
```

`r3` 不是无关保留值。低区 mode0 在需要擦除时恢复整个 4096 字节扇区；高区 mode1 在需要擦除时只恢复本扇区前缀到本次写入末尾，后部变成 `FF`。如果目标范围原来全是 `FF`，可走直接 program 快路，尾部则不被擦掉。跨扇区分段处理。实际 Flash RMW 的独立重放见 [storage-emulation.json](evidence/storage-emulation.json)；不能把 mode1 当成任意位置无损覆盖接口。

写回应总是只有 8 字节 body：

```text
0x02C6:u16 | 4:u16 | address:u32
```

**ACK 不等于已执行写入，更不等于断电后持久化成功。** 一般 gate 或上述特殊 flag 使写入跳过时，固件仍会发同样 ACK。协议里这条回应没有字节计数、写入状态或内容 CRC；还需要独立读回和状态上下文才能建立更强判断。当前工具没有真机读回流程。

## 8. 其余 command 的静态定位

以下是 dispatcher `0x08003468` 中的可见分支。只有 hello、原始存储读写及帧层达到本页详述程度；其它名称保留推测范围。工具不提供这些命令的在线发送接口。

| command | handler / 路径 | 当前结论 |
|---|---|---|
| `0x02BC` | `0x08002D38` | session / hello，包含 RTC 副作用 |
| `0x02C3` | `0x0800320C` | 外部 Flash 读取 |
| `0x02C5` | `0x0800334C` | 外部 Flash 写入 |
| `0x02C7 / 0x02DF` | `0x08002F6C`，第三参数 0/1 | RF / VFO 配置路径，字段未完全核验 |
| `0x02C9` | `0x080030A0` | RF / VFO 配置相关，回应 `0x02CA` |
| `0x02CF` | `0x080031B8` | 下游 `0x08017F98` 读射频状态，回应 `0x02D0` |
| `0x02D1` | `0x080031E0` | 下游 `0x0801735C` 测量，回应 `0x02D2`；具体物理单位待核验 |
| `0x02D5` | `0x08002E28` | challenge / 认证相关路径；没有实现绕过 |
| `0x02D7` | `0x08002C74` | 模式 / 会话转换，调用同一 hello reply |
| `0x02D9 / 0x02E0` | `0x08002EB4`，第三参数 0/1 | Si4732 调谐路径 |
| `0x02DB` | `0x08003184` | Si4732 状态读取，回应 `0x02DC` |
| `0x02DD` | `0x080032E4` → `0x0800542C` | RF 数据发送相关；不能当成信道写入 |
| `0x0320` | `0x08002C20` | 拷贝 16+6 字节到 RAM，回应 `0x0321` 经 transport2 |
| `0x0322 / 0x0323` | `0x08002C08 / 0x08002BE8` | 更改 `0x20000162` 软件状态 |
| `0x05DD` | dispatcher 内 `0x08003570` 附近 | AIRCR 系统复位路径 |

## 9. 官方 CPS 0.56 的独立交叉证据

厂商下载记录 [11390](https://cms.qsfj.com/api/entries/files/11390?fl-language=en) 提供 `UV-K10-Pro-0.56.rar`，来源摘要见 [hardware-research.md](hardware-research.md)。RAR 内安装器采用 Setup Factory 7；本轮按其容器格式和 PKWARE DCL 静态解包，11 个应用文件的解压长度、CRC32 均匹配，数据流恰好结束于文件尾。此为前置外部解包流程的记录，`analyze_cps.py` 只核验已经提取的主程序，不重复解包或重新声称验证安装包内所有 CRC。没有运行安装器、主程序或 DLL。

| 对象 | SHA-256 / 身份 |
|---|---|
| 安装器 `UV-K10 Pro(0.56 客户版).exe` | `ad891682d9e30893d1ae4d579cc7a2d92fd84a2ff9b7c5333a6138e2c21c29ba` |
| 解出的主程序 `UV-K10 Pro.exe` | `79c9b3429187f7fca13cc8971d0e5fd46184f078127f149f333a267b99815132`，910,336 字节 |
| 代码形式 | .NET 元数据与 IL；相关类命名空间为 `K7`，不能据命名空间推导机器型号兼容性 |

可复现摘要在 [cps-protocol-crosscheck.json](evidence/cps-protocol-crosscheck.json)。以下 RVA 指 **CPS 主程序**，不同于前面 MCU 的绝对地址：

| CPS 方法 / 元数据 | RVA / token | 交叉确认结果 |
|---|---|---|
| `protocol_struct.makefram` | RVA `0x49934`，token `0x060002B6` | `AB CD`、实际 body 长度、CRC、`DC BA`；XOR 覆盖 body+CRC |
| `Iparse.CRC16CCITT` | RVA `0x63750`，token `0x06000396` | 名称虽为 CCITT，代码参数确为 X25 的 `FFFF/8408/最终取反` |
| `protocol_struct.ContentNorOr` | RVA `0x49A88` | 使用的 16 字节表与固件逐字节相同 |
| `protocol_struct.Read` | RVA `0x49E88`，token `0x060002BD` | command 707、inner 12、实际 body16，数据从回应 +12 取出 |
| `protocol_struct.Write` | RVA `0x4A298`，token `0x060002C2` | command709、inner12、固定128字节数组；只检查回应 command710 即返回成功 |
| `MSG_CALMS_ParaWriteReq` | token `0x02000055`，Pack=1 | 实际结构长144；count字段UInt16；固定数组长128 |
| `wfm_progress.ReadBlock / WriteBlock` | RVA `0x7FAF8 / 0x7FB80` | 每次取 `min(128, remaining)` |
| `wfm_progress.WriteChannel` | RVA `0x7FC08` | 遍历999个64字节记录；raw attribute<7时写记录；最后写0x11000起1024字节属性块 |
| `protocol_struct.Link` | RVA `0x49CEC` | command700、inner4、实际body28，session及UTC时间 |
| `ComPort` 构造函数 | RVA `0x49278` | `SerialPort`配置38400、8数据位、1停止位、RTS关闭 |

这些结果说明为什么必须同时看主机和设备：仅从 handler 推导“内层长度应等于全部 payload”，会错误拒绝原厂 CPS 的正常写帧。CPS 把 `Write` 返回成功的判断限定到回应 command，同样没有消除设备侧 ACK 不等于真正执行写入的歧义。

属性筛选还有具体版本差异：此 CPS 的 `WriteChannel` 比较完整 raw byte 是否 `<7`；8.02.16 固件信道装载先看属性的低半字节，再结合相应频段是否启用。带非零高半字节的属性可能被固件按有效频段解释，却不被这个 CPS 写入循环选中。离线 codec 保留高半字节，不强行把两套筛选条件合成同一规则。详见 [channel-storage.md](channel-storage.md) 的固件和 CPS/CSV 对照。

静态提取使用了 [sfextract](https://github.com/CybercentreCanada/sfextract) 的 PKWARE 解码，并参照 [xtsf 的 Setup Factory 格式实现](https://binref.github.io/units/formats/archive/xtsf.html) 检查容器；这些工具和厂商 payload 留在分析环境，未复制到项目。仓库工具只输出结构、所选常量与地址等事实摘要，不发布完整主程序反编译。

## 10. 使用与复现

标准库测试不需要原厂样本：

```sh
python3 -m unittest discover -s tests -p test_programming_protocol.py -v
```

现有 30 项标准库测试覆盖标准 CRC 向量、真实 sender 对照帧、CPS固定数组与紧凑格式、数据损坏、回应占位语义、会话、零填充、特殊区域 gate、ACK 歧义及完整区间限制。另有 1 项 `K10_REFERENCE_RAW` 可选门控测试：未提供样本时 skip，提供对应解码文件和 Unicorn 后执行全部协议原指令实验。

打印一个本地读请求草稿：

```sh
python3 tools/programming_protocol.py draft-read 0x000000 64 \
  --session 0x12345678 --transport 1 --xor
```

检查自己提供的捕获文件：

```sh
python3 tools/programming_protocol.py inspect captured-frame.bin \
  --direction response --transport 1 --xor
```

文件必须恰好含一帧。方向、transport 和 XOR 状态需要显式给出；工具不会把一个固定 `FFFF` 的回应误当成通过请求 CRC 的数据。

重放精确官方样本时，先按 [requirements-re.txt](../requirements-re.txt) 安装可选逆向依赖，并自行取得、解码对应原厂文件：

```sh
python3 tools/emulate_programming.py /path/to/decoded-reference.raw.bin \
  --output /tmp/programming-protocol.json
```

重放要求 SHA-256 完全一致；基础 42 项包括 1 组实际 CRC、8 组 parser、27 组 parser→dispatcher→实际 sender、6 组独立 sender。USB 描述符另行静态解析。报告完整列出每个 hook 的地址、参数、合成输入、回应和指令数量，没有内嵌原厂代码或完整反汇编。

若自行静态提取了精确版本的 CPS 主程序，可安装可选 `dnfile==0.18.0` 和 `dncil==1.0.2`，运行：

```sh
python3 tools/analyze_cps.py '/path/to/UV-K10 Pro.exe' \
  --output /tmp/cps-protocol-crosscheck.json
```

这只解析 .NET 元数据和所选 IL，不启动 CLR、不执行 CPS。

### 原协议到合成 NOR 的完整读写回环

报告另有 `synthetic_nor_roundtrip`，使用 [channel_codec.py](../tools/channel_codec.py) 制造全人工信道记录，索引明确为 **zero-based 77**，记录地址 `0x1340`，属性地址 `0x1104D`。记录包含 `MODEL77` 名称、145.5 MHz RX、600 kHz offset 等人工值，属性字节显式给定为 `02`，不代表从频率自动推导或真机已有数据。

这组实验默认使用 CPS 0.56 的 inner12/body144写帧，保留实际 parser、dispatcher、读写 handler、RMW、分页面函数和 USB sender。只在 `0x080195E4 / 0x08019588 / 0x080197EC` 替换 NOR 的 read、erase、page program，另外明确替换 UI 和 USB 外设发送边界。两个目标扇区先填非 `FF` 人工模式，使实际 RMW 必须经历擦除和重写。

| 事务 | 原指令与合成存储的结果 |
|---|---|
| 写 64 字节信道记录 | 实际 RMW 更新记录；同扇区邻接数据保留 |
| 写独立 1 字节属性 | 实际 RMW 更新属性；属性扇区其余字节保留 |
| 读回信道 | 实际 USB 回应的 64 字节逐字节等于人工记录 |
| 读回属性 | 实际回应数据等于 `02` |
| 打开合成 gate 后尝试另一名称 | 仍得到同样写 ACK；没有 NOR 操作，数组完全不变 |
| gate 状态下再次读取 | 回应 64 个零；没有 NOR 读调用 |

最终比较整个 2 MiB 合成数组，确认除这 65 个目标字节外全部保持原值。2 MiB 是模型输入，不是此实验测得的实物容量。这个回环连接了“协议写调用”与“扇区更新算法”的证据，但仍没有证明物理 Flash 编程、真实 USB 时序或掉电持久性。

下一步最有价值的是取得真实 CPS/USB 抓包，核对 hello 可选位置字段的单位、RTC 与 session 生命周期、gate 的来源，以及读写后何时要求重新加载配置。128 字节主机分块已由 CPS IL 确认；实测仍要核对所用界面分支与具体硬件版本。仓库的离线协议分析不能视作已完成可连接真机的第三方写频软件。
