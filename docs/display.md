# UV-K10 Pro 显示、屏幕连接与 RGB 背光

## 范围与结论等级

本页只分析 **UV-K10 Pro / K10PRO_ENG_v8.02.16**，应用映射起点为
`0x08002000`。解码后样本 SHA-256：

`061759672cc69a1b00420ce6fae67b0756d3db111f8fa4329cadc940dcd17f17`

没有使用 UV-K1、UV-K5、UV-K5(8) 或其他型号的接线定义。下文区分三类证据：

1. **原厂指令执行观察**：在 Unicorn 中执行该 SHA 固定样本的函数，记录 GPIO、
   SPI 和 PWM 寄存器访问。可复现结果见
   [display-analysis.json](evidence/display-analysis.json)，工具为
   [emulate_display.py](../tools/emulate_display.py)。
2. **控制器资料匹配**：将已经得到的命令、寻址和页格式与 Sitronix 原厂资料比较。
   这是兼容族判断，不是确认板上芯片丝印。
3. **仍需 PCB/实机核实**：排线触点序号、MCU 封装脚号、供电电压、屏幕复位的实际
   接法、背光晶体管与 LED 极性，以及玻璃安装方向。

已执行路径支持：**128 × 64、1 bit/pixel 的页面式图形缓冲区，SPI1 四线写接口，
三路独立 PWM 背光**。可调 RGB 指的是背光通道；原厂刷屏流没有 RGB 像素数据。

## MCU 侧连接定义

以下是 MCU 的端口信号名，不是 LCD 排线针脚编号。

| 信号 | MCU 引脚 | 原厂配置 | 执行观察 |
|---|---|---|---|
| LCD 串行时钟 SCK | **PA5** | mode 2 / AF **0** / push-pull / speed 2 / pull-up | SPI1 初始化 `0x0801A1D8` 配置 |
| LCD 串行数据 MOSI | **PA7** | mode 2 / AF **0** / push-pull / speed 2 / no-pull | 同上；未配置 LCD MISO |
| LCD CS# | **PB2** | GPIO output / push-pull / speed 3 / pull-up | 低电平有效；每个记录到的 SPI 字节都在 CS# 低时发送 |
| LCD D/C，亦称 A0 | **PF2** | GPIO output / push-pull / speed 3 / pull-up | 低电平发命令，高电平发显示数据 |
| 红色背光控制 | **PB6** | mode 2 / AF **11** / push-pull / speed 2 / no-pull | TIM1_CH3；设置字段与 PWM 调用链闭环，见下节 |
| 绿色背光控制 | **PA15** | mode 2 / AF **2** / push-pull / speed 2 / no-pull | TIM2_CH1 |
| 蓝色背光控制 | **PB5** | mode 2 / AF **1** / push-pull / speed 2 / no-pull | TIM3_CH2 |

GPIOA、GPIOB、GPIOF 基址分别为 `0x50000000`、`0x50000400`、`0x50001400`。
PB2/PF2 由通用初始化 `0x08018B94` 设置为输出，且先写高电平锁存值再配置输出模式。
CS#、D/C 的低/高分别通过 GPIO `BRR +0x28` / `BSRR +0x18` 操作。

这里的 AF 来自实际执行后的 `AFRL/AFRH`，不是根据其他 Puya 子型号猜测。
GPIO 参数结构的 `+0x10` 是 pull，`+0x14` 才是 AF：PA5 的 pull-up 值 1
不能误读为 AF1。两条 SPI 引脚均为 **AF0**。

已执行的 LCD 初始化与刷新函数没有切换额外的 RESET 或电源 GPIO；初始化发送
`E2` 软件复位。这不能排除板上存在硬件复位线、共用复位、RC 复位或固定供电。
没有排线照片/连通性测量时，不能补写这些信号的具体针脚。

## SPI1 和字节传输

SPI1 基址为 `0x40013000`。原厂初始化后的寄存器为：

| 寄存器 | 值 | 含义 |
|---|---|---|
| CR1，启动前 | `0x031F` | 主机，8 bit，MSB first，CPOL=1、CPHA=1，软件 NSS，SSI=1，BR=3 |
| CR1，启动后 | **`0x035F`** | 以上配置加 SPE；SPI mode 3，PCLK/16 |
| CR2 | `0x0000` | 此路径没有打开 SPI DMA 或中断 |
| I2SCFGR | `0x0000` | SPI 模式 |

`0x0801A27C` 每次先轮询 SR.TXE，向 DR `+0x0C` 写 **1 byte**，再轮询 RXNE
并读出接收数据。虽然 LCD 没有配置 MISO，SPI 主机仍有接收状态需要处理。
这些调用均为 CPU 轮询，未见 LCD DMA 提交；这个结论仅覆盖已识别的显示路径。
实际 SCK 频率还取决于运行时 PCLK，不从分频值推定固定 MHz 数。

## 初始化命令与控制器匹配

`0x080194A0` 先初始化 SPI1，拉低 CS#，依次发送：

```text
E2                         delay(120)
A2 C0 A1 A6 A4 24 81 1F 2B delay(1)
2E                         delay(1)
2F 2F 2F 2F                delay(40)
40 AF
```

随后释放 CS#、设置两个 framebuffer 指针并清屏。延时函数 `0x0801D000` 把参数
乘 1000 后调用微秒延时 `0x0801D010`，因此这里的 120 / 1 / 1 / 40 是毫秒参数；
仿真只记录参数，没有验证晶振和物理等待时间。完整初始化共有 **1067 个 DR 写字节**：
17 个初始化命令字节，加清屏时的 26 个寻址/起始行命令字节和 1024 个零数据字节。

在 Sitronix **ST7565R/ST7567 兼容命令族**中，这组字节可以解释为：

| 字节 | 资料中的功能 |
|---|---|
| `E2` | 软件复位 |
| `A2` | bias 选择；1/65 duty 时对应 1/9 bias |
| `C0`、`A1` | COM 正向、SEG 反向 |
| `A6`、`A4` | 正常显示、按 RAM 数据显示 |
| `24` | 内部稳压电阻比选择值 4 |
| `81 1F` | electronic volume，值 31 |
| `2B`、`2E`、`2F` | 内部电源控制的不同位组合；最后为三项全开 |
| `40`、`AF` | 起始行 0、显示打开 |
| `B0..B7`、`10`、`04` | 页 0..7、列高/低位；起始列 4 |

这组解释对应 Sitronix 原厂命令表，不应据此声称已经识别出唯一型号。
ST7565R 与 ST7567 等多个型号具有 132 × 65 RAM 和四线串行接口，见
[Sitronix 官方产品表](https://www.sitronix.com.tw/en/products/industrial-display-driver-ic/mono-stn-lcd-driver-ic/)。
参照文档为 Sitronix
[ST7565R v1.7，Topway 保存的原厂 PDF](https://www.topwaydisplay.com/sites/default/files/2020-04/ST7565R%20V17_960601.pdf)
（pp. 24–28、41–50）及
[ST7567 v1.5，Newhaven 保存的原厂 PDF](https://newhavendisplay.com/content/datasheets/ST7567.pdf)
（pp. 12–17、21–27）。这些是控制器厂商资料的显示模组厂镜像，均不是 K10 PCB 证明。

## 帧缓冲与刷新区域

原厂缓冲区位于 **`0x20000D70`，1024 bytes**，布局为 8 页 × 128 列：

```c
byte_offset = page * 128 + x;   /* page 0..7, x 0..127 */
```

每一列数据字节对应同页的 8 个纵向像素。控制器资料的常规页格式中 D0 对应该页
第一条 COM 线，D7 对应第八条；在独立绘图层中可用 `1u << (y & 7)`、页号 `y >> 3`。
具体屏幕左右/上下方向仍受 A1/C0 设置和玻璃走线影响，需要屏上非对称测试图确认，
不能把资料中 COM/SEG 编号直接当成屏幕外观坐标。

| 函数 | 观察到的职责 |
|---|---|
| `0x08019574(command)` | D/C 低后发送 1 byte，不自行包裹 CS# |
| `0x0801954C(column,page)` | 发送 `B0+page`、列高 4 bit、列低 4 bit；不自行加偏移或包裹 CS# |
| `0x08019434()` | 从 `[0x2000000C]` 取指针，刷新 page 0 的 128 bytes |
| `0x080193E0()` | 从 `[0x20000010]` 取指针，刷新 page 1..7，每页 128 bytes |
| `0x08019390(x,page,length,data,flag)` | 区域传输；自动加列偏移 4；flag=0 发 data，flag 非零发零 |
| `0x08019478(fill)` | 填充 1024 bytes、使 NOR 缓存标记无效，再调用两段整屏刷新 |

初始化把 page 0 指针设为 `0x20000D70`，page 1..7 指针设为 `0x20000DF0`。
每页传输前都重新设置 page 和 column；使用的控制器列地址是 **4..131**。
原厂把顶部 8 行与其余 56 行分成两个刷新入口，这与状态栏单独刷新相符；状态栏解释
属于用途推断，两个实际传输区域则已由执行记录确认。

局部刷新用合成数据 `01 80 55 AA FF` 验证：输入 `x=7,page=3,length=5`，命令是
`B3 10 0B`，随后原样传输 5 bytes；flag=1 时传输 5 个零。代码没有在这个底层接口
加入屏幕边界裁剪，新驱动需要自己验证参数。

### 与 NOR 缓存共用 RAM

`0x20000D70` 同时是原厂 4 KiB NOR 扇区缓存的起点。LCD 清屏函数将
`[0x20000048]` 写为 `0xFFFFFFFF`，即缓存扇区标记无效。重放以非零 framebuffer、
有效缓存标记为输入，验证初始化确实清零并改变标记；另用 `0xA5` 填充验证仅改动前
1024 bytes，后续哨兵数据保持。

独立驱动接收调用者自己的 1024-byte buffer，不复用原厂 NOR 缓存地址。这个别名关系
也解释了为何分析原厂全局 RAM 时不能把两段完全独立分配。

## RGB PWM 与设置字段的闭环

背光初始化 `0x0801A5F4` 配置上述三个 AF 引脚，并设置各定时器：

- PSC=`15`，ARR=`2500`，初始比较值为 0，向上计数。
- PWM1 模式，通道为 active-high，通道输出使能，CR1.CEN=1。
- 原厂还向三个定时器的 `+0x44` 写 bit15；具体寄存器实现需遵循对应 MCU 手册。

设置函数 **`0x0801A710(r,g,b)`** 的寄存器效果：

| 颜色字段 | NOR 原始偏移 | RAM 值 | PWM 输出 | 比较值 |
|---|---|---|---|---|
| Red | `0x13024` | `0x20002404` | PB6 / TIM1_CH3 / CCR3 `+0x3C` | `r * 25` |
| Green | `0x13025` | `0x20002405` | PA15 / TIM2_CH1 / CCR1 `+0x34` | `g * 25` |
| Blue | `0x13026` | `0x20002406` | PB5 / TIM3_CH2 / CCR2 `+0x38` | `b * 25` |

字段的颜色标签来自 K10 Pro 写频软件/APP schema，见
[settings-map.md](settings-map.md) 的 RGB 设置。设备端链路已另行验证：

1. `0x0800F376..0x0800F380` 从 NOR `0x13000` 读 `0x128` bytes 到共享缓冲区。
2. `0x0800F530/F540/F550` 读取偏移 `0x24/25/26`，各自允许 0..100，超过 100
   替换为 **50**；对应写入点为 `F53E/F54E/F55E`。
3. `0x0800E36C` 在状态条件允许时取三个 RAM 字节，调用 `0x0801A710`。
4. 设置保存入口 `0x08010254` 的 bit0 分支把 `0x200023E0` 开始的 `0x128` bytes
   写回 NOR `0x13000`，与上述字段偏移一致。

仿真执行 **F376 起、F560 之前停止的明确窗口**，r4/r5/r6 的入口状态由实验提供；
没有声称执行整个通用设置初始化。NOR 读取由共享 NorModel 提供合成字节，之后的
校验、RAM 写入、配置应用和 PWM 乘法均执行原厂指令。结果包括：

| 输入 R/G/B | 校验后 RAM | CCR3/CCR1/CCR2 |
|---|---|---|
| 13 / 47 / 89 | 13 / 47 / 89 | 325 / 1175 / 2225 |
| 0 / 100 / 1 | 0 / 100 / 1 | 0 / 2500 / 25 |
| 101 / 255 / 100 | 50 / 50 / 100 | 1250 / 1250 / 2500 |

100 对应比较值 2500，不能把它当成严格 100% DC；计数周期由 ARR+1 决定。
PWM 频率是 `timer_clock / (16 * 2501)`，实际值需计入时钟树。active-high 是 MCU
定时器的输出配置；背光驱动晶体管可能进一步改变电气极性，仍需要 PCB 核实。

## 仿真边界与复现

CPU 执行真实样本指令；GPIO 模型提供寄存器存储和 BSRR/BRR 输出锁存效果。
SPI SR 的 TXE/RXNE 被人为置为就绪，DR 读取返回 0；不模拟串行时序、屏幕内部电路
或返回芯片 ID。定时器只记录寄存器，不推进计数。延时被替换为记录函数参数。
保留外设地址白名单，未建模寄存器访问会失败，不默默返回零。

```sh
python -m pip install -r requirements-re.txt
python tools/emulate_display.py /local/path/K10PRO_ENG_v8.02.16.raw.bin \
  --output docs/evidence/display-analysis.json
K10_REFERENCE_RAW=/local/path/K10PRO_ENG_v8.02.16.raw.bin \
  python -m unittest discover -s tests -p 'test_display_emulation.py' -v
```

仿真流程执行 14 个场景：LCD 初始化 1、整帧模式 1、局部刷新 2、清屏填充 1、
直接 PWM 5、RAM 设置应用 1、NOR 设置到 PWM 3。原厂 raw 文件必须由使用者自行取得；
仓库不包含厂商镜像、字体、完整反汇编或拆机图片。

## 独立固件驱动

[display.c](../firmware/src/display.c) 与
[display.h](../firmware/include/k10/display.h) 是独立编写的 C 驱动。
它使用上述已观察到的引脚、mode 3 / PCLK16、17-byte 初始化命令和毫秒延时，
并保持 page 0 与 page 1..7 的两段刷新形式：

| API | 行为 |
|---|---|
| `display_init()` | 配置 SPI1、LCD GPIO 与三路 PWM，发送初始化和零帧，背光比较值保持 0 |
| `display_present(buffer,length)` | 只接受非空且恰好 1024 bytes 的 buffer；不修改或保留调用者数据 |
| `display_set_backlight(r,g,b)` | 接受各通道 0..100；非法参数不会改变当前比较值 |
| `display_last_error()` | 返回 OK / DISABLED / NOT_INITIALIZED / INVALID_ARGUMENT / TIMEOUT / BUS_ERROR |

`K10_ENABLE_DISPLAY=0` 编译分支不访问 MMIO，也不等待硬件。启用时驱动只配置自己的
引脚，RCC 时钟/复位寄存器使用读改写；TIM1、TIM2、TIM3 由背光驱动独占。独立实现
只对高级定时器 TIM1 设置 BDTR.MOE，不把原厂通用辅助函数对 TIM2/3 的 `+0x44` 写入
机械复制到驱动。未配置任何猜测的 LCD RESET、电源或射频 GPIO。

SPI 等待同时受 10 ms deadline 和 100000 次轮询限制，系统 tick 停止也能退出。
开始事务前清理旧 RX，发送每一字节都读出 RX；结束时等待 TXE 置位、BSY 清零后再释放
CS#。超时或 SPI 错误会释放 CS#、把背光比较值清零，并要求重新初始化；再次初始化
只复位所拥有的 SPI1 和三个 PWM 定时器。API 成功代表本地 SPI 事务完成，写接口本身
不能证明 LCD 已连接或收到数据。

主机测试直接编译实际 `display.c`，使用独立 MMIO 模型与 UBSan，覆盖 9 个启用场景
和 1 个禁用场景：初始化/像素顺序/边界、旧 RX、TXE/RXNE/BSY 超时、SPI 错误、tick
回绕和停滞、故障后的恢复、三路 CCR，以及关闭后无 MMIO。测试还检查相关 GPIO 操作
没有改动矩阵行、I²C 和调试器引脚。

```sh
python -m unittest discover -s tests -p 'test_display_driver.py' -v
```

主机模型与指令仿真均不代替物理 LCD 的电压、走线、时序及方向验证。

## 尚待补齐的物理定义

目前能够编写 MCU 侧显示驱动，但无法从应用代码确定排线接点的物理顺序。下一份
有效硬件证据应能同时看到 MCU 封装方向、LCD 排线/焊盘和对应走线，或提供断电连通性
记录。需要逐项确认的仍有：

- CS#、D/C、SCK、MOSI 在排线上的触点序号，以及电源、地、RESET。
- 屏幕玻璃/模组型号，确认 ST7565/ST7567 兼容族中的具体芯片或兼容品。
- PB6/PA15/PB5 到 RGB LED 的驱动器件、限流和有效电平。
- 非对称像素测试图的真实方向、列偏移和可视范围。

这些未知项没有用其他机型的拆机图补齐。
