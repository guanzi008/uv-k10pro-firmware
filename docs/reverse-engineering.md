# 官方样本与独立实现依据

本记录把文件内直接可见的事实、与厂商资料的对应，以及仍需实物确认的内容分开。地址均指下面指定的 **8.02.16 英文样本**；不能假设其他版本函数地址一致。

## 样本来源

| 项目 | 值 |
|---|---|
| 厂商产品页 | <https://qsfj.com/products/11145> |
| 厂商公开下载记录 | <https://cms.qsfj.com/api/entries/files/11434?fl-language=en> |
| 下载记录标题 | UV-K10 Pro English Firmware |
| 文件名 | `K10PRO_ENG_v8.02.16_publish.bin` |
| 原文件长度 | 118,974 字节 |
| 原文件 SHA-256 | `aa33d01a06c5f013ef46afb6c6a37dd16b15318d0e6409bbadfaba53bdb5ad5b` |
| 容器内版本 | `8.02.16` |
| CRC16/XMODEM | `0xC486`，与存储值一致 |
| 解码后长度 | 118,956 字节 / `0x1D0AC` |
| 解码后 SHA-256 | `061759672cc69a1b00420ce6fae67b0756d3db111f8fa4329cadc940dcd17f17` |

厂商记录指向的文件链接：
<https://www.dropbox.com/scl/fi/t3hjej7ao9rpv661o8v7h/K10PRO_ENG_v8.02.16_publish.bin?rlkey=3r9820ejabye5svswno24f2o0&st=bwzucjio&dl=1>

链接内容可能随厂商更新而变化，应以 SHA-256 识别分析对象。仓库只保存摘要和独立实现，不保存该二进制、完整反汇编或厂商资源。

## MCU 指纹与内存布局

| 观察 | 样本证据 | 能够支持的结论 |
|---|---|---|
| 应用重定位 | `0x0801BBF8` 的系统初始化写 VTOR=`0x08002000` | 本应用的向量基址是 `0x08002000` |
| 初始向量 | SP=`0x20003E20`，Reset=`0x080020D5` | Thumb 微控制器应用；SP 位于 16 KiB SRAM 地址范围 |
| 向量数量与保留项 | 48 项；IRQ30 对应项为零 | 与 F071 中断布局一致；不能仅凭此排除所有兼容芯片 |
| GPIO/EXTI | IRQ7 的实际处理代码访问 GPIOB=`0x50000400`、EXTI=`0x40021800` | 匹配 PY32F071 的外设布局 |
| TIM16 | IRQ21 处理代码访问 `0x40014400` | 匹配 PY32F071 TIM16 |
| USB | IRQ31 处理代码访问 `0x40005C00` | 匹配 PY32F071 USB / IRQ31 |
| UID | `0x08018B0C` 从 `0x1FFF3000` 读取 16 字节 | 匹配 PY32F071 UID 区域 |
| SRAM 初始化 | `0x0801F008..0x0801F028` 的两条初始化记录，目标从 `0x20000000` 连续覆盖 15,904 字节 | 超出 8 KiB 档容量，符合 16 KiB 规格档 |
| 应用末尾 | 基址加长度=`0x0801F0AC`，结束地址不包含在内 | 超出 64 KiB Flash 档，符合 128 KiB 规格档 |

综合证据指向 **PY32F071 或寄存器/中断兼容实现、128 KiB Flash / 16 KiB SRAM 规格档**。Puya 的 F071 资料定义 Cortex-M0+、最高 72 MHz。现有固件证据不能读出芯片丝印、封装后缀或直接测量晶体频率，仍需实物确认。

原厂 RAM 初始化表先将压缩数据展开到 `0x20000000`，长度 `0x4C4`，随后从 `0x200004C4` 清零 `0x395C` 字节。结束位置是 `0x20003E20`。这不是“15,904 字节都是全局变量”的证明；栈和缓冲区也可能计入该布局。

原厂应用前 8 KiB 不在下载包内，不能据此断言已经分析了整个 bootloader。新工程把应用范围限制为 `0x08002000..0x0801EFFF`，暂不使用顶端 4 KiB；这个边界是工程保守选择，**不是已确认的校准分区定义**。

## 时钟

样本 `0x0801BBE8` 附近把软件时钟变量设置为 48,000,000，随后 SysTick 初始化以该变量计算重装值。这里的写入并不配置 PLL，不能直接证明真实硬件运行频率。系统时钟可能由未包含在文件中的启动程序设置。

本工程保留已继承的 RCC 配置，通过当前寄存器推导 HCLK；无法确立输入时钟时在诊断结构中清除 `K10_FLAG_CLOCK_KNOWN`。延时和超时在未知时钟情况下仅可作为有界的诊断尝试，实机应测量时钟后再把结果认定为准确。详见 `firmware/src/hal.c`。

## SPI NOR 和 Si4732

详见 [pinmap.md](pinmap.md)。关键限制是：

- 原厂协议中的 `0x001FF000` 是起始地址检查常量，不能当作 Flash 容量寄存器。
- 新驱动通过 JEDEC 识别支持的精确型号；未知 ID 保留原始值并拒绝数组读取，不根据一个容量字节随意猜测芯片。
- Si4732 GET_REV 的 PN=`0x20` 是依照 Skyworks AN332 的编号规则解释的。检测代码存在不代表所有 K10 Pro 硬件版本都焊有 Si4732。
- 样本 `0x08019AF4` 从外部 Flash 的 `0xA0000` 读取 `0x3DD8` 字节并按 8 字节分组发送给接收芯片。相应补丁数据不在 MCU 更新包内，本工程不附带或调用该补丁。

## 一手参考资料

- [Puya PY32F071 产品资料页](https://www.puyasemi.com/py32f071xilie275/2831.html)：用于核对系列规格，不作为 K10 Pro 具体封装的证据。
- [PY32F071 数据手册 v1.4](https://www.puyasemi.com/download_path/%E6%95%B0%E6%8D%AE%E6%89%8B%E5%86%8C/MCU%20%E5%BE%AE%E5%A4%84%E7%90%86%E5%99%A8/PY32F071%E7%B3%BB%E5%88%97%E6%95%B0%E6%8D%AE%E6%89%8B%E5%86%8C_V1.4.pdf)：型号容量、存储器映射、复用引脚。
- [OpenPuya PY32F071 SDK](https://github.com/OpenPuya/PY32F071_Firmware)：CMSIS 设备头和 LL RCC 定义，用于核对寄存器位意义。没有把整个厂商 SDK 复制进本项目。
- [Skyworks AN332](https://www.skyworksinc.com/-/media/Skyworks/SL/documents/public/application-notes/AN332.pdf)：POWER_UP、GET_REV、FM_TUNE_FREQ、FM_TUNE_STATUS、CTS/ERR、I²C 地址和响应编码。
- [F4HWN 更新包 codec](https://github.com/armel/uv-k1-k5v3-firmware-custom/blob/main/tools/serialtool/_fwcodec.py)：公开的同类容器协议参数，用于交叉核对。容器兼容不代表板级固件或链接布局兼容。

## 实现与验证声明

新代码根据上述行为与公开接口独立编写。参与实现者看过原厂反汇编，因此不称为严格隔离的 clean-room 工程。对来源、依赖和许可证的处理见 [NOTICE](../NOTICE)。

本轮验证只包括主机测试、交叉编译、结构检查和参考文件的字节级回环。没有任何实际 USB 刷写、SWD 写入、运行中 GPIO 测量或射频测量记录。
