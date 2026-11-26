# 软件验证记录

记录日期：2026-11-26。前半部分保留 `0.1.0-exp` 初始实现的结果，末节记录继续逆向后的扩展验证。验证地点为 Linux x86_64 主机；没有连接无线电设备。后续代码变更请以相应提交的 CI 与实际测试记录为准。

## 构建环境与结果

使用 Arm 官方 GNU Toolchain **14.3.Rel1 / GCC 14.3.1 20250623**，`-mcpu=cortex-m0plus -mthumb -mfloat-abi=soft -Os -ffreestanding -nostdlib`，开启 `-Wall -Wextra -Werror`，链接 `libgcc`。

工具链归档 `arm-gnu-toolchain-14.3.rel1-x86_64-arm-none-eabi.tar.xz` 的 SHA-256 已与[厂商校验文件](https://developer.arm.com/-/media/Files/downloads/gnu/14.3.rel1/binrel/arm-gnu-toolchain-14.3.rel1-x86_64-arm-none-eabi.tar.xz.sha256asc)核对：

```text
8f6903f8ceb084d9227b9ef991490413014d991874a1e34074443c2a72b14dbd
```

| 项目 | 实测结果 |
|---|---|
| 原始应用大小 | 4,724 字节 |
| 应用地址范围 | `0x08002000..0x08003273` |
| 向量表 | 48 项；保留项/IRQ30 为零；处理函数均为镜像内 Thumb 地址 |
| 初始 SP | `0x20004000`，8 字节对齐 |
| Reset 向量 | `0x08003039` |
| GNU size | text=4,720，data=4，bss=2,588 |
| RAM 分配 | 共 2,592 字节，已包括 2,048 字节主栈与 256 字节异常栈预留 |
| 未解析符号 | 无 |
| 更新容器长度 | 8,214 字节，原始应用补至 `0x2004` 后加入 18 字节包装 |
| 容器版本 / CRC | `0.1.0-exp` / `0xDF25` |

原始应用 SHA-256：

```text
72b354efc8b6aceb4c765571e87577025e3ad4dbaba0210d302b737616c2be39
```

实验容器 SHA-256：

```text
fa572a0f56c22ca8fc794206c089aff907250c26c9a6668264e37a3540e31663
```

这些摘要属于上述工具链与默认配置的本地构建。CI 采用发行版工具链，编译器版本不同可能产生不同的字节、大小与摘要；CI 检查的是结构和软件行为，不强制匹配此二进制摘要。

## 主机测试

提供固定官方参考文件路径后执行 `make test`：**23 个 discover 测试全部通过，另外 2 个驱动测试全部通过，无跳过**。

- 容器与镜像检查共 17 项：CRC 标准值、腐损拒绝、版本字段、边界、短镜像填充、CLI 覆盖保护、向量范围与 Thumb 位、SP 对齐，以及官方样本逐字节回环。
- HAL 共 6 项测试：执行实际 `hal.c`，覆盖 19 组 RCC 解码情况及外部频率配置、切换快照、tick/延时回卷、重复初始化；不是另写一个时钟算法作对照。
- 驱动的 2 项测试包含 **17 个独立场景**，执行实际 C 驱动并启用 UBSan。覆盖 SPI 只读命令、地址顺序、末端/溢出/未知型号、传输失败释放 CS、busy/FAULT/超时；I²C 真实边沿模拟下的正常命令、NACK、卡线、拉伸、CTS/命令错误、错误 PN、调谐超时/频率不匹配，以及禁用配置的零寄存器访问。

官方参考文件未放进仓库，所以普通 CI 会明确跳过其中 1 个参考文件测试；其他测试不需要该文件。

## 构建配置回归检查

同一个构建目录先生成默认镜像，再修改 `VERSION` 并禁用两项硬件驱动，确认对象重新编译、版本字符串确实保留在最终镜像中、镜像缩小至 2,256 字节。恢复默认选项后，原始应用与前一次默认构建逐字节相同。再次执行 `make package` 成功且不会无故重编译。

## 尚未完成的验证

没有执行实机启动、刷写器接受性、bootloader 恢复、外部 Flash 实际容量、GPIO/总线波形、晶振频率、屏幕、键盘、接收音频或发射测量。模拟测试与文件校验不能替代这些结果。

## 第二轮：信道、协议、应用交叉与原指令仿真

本轮使用同一固件样本，另行静态检查官方 CPS 0.56、官方 APK 文件名标签 64 及经销商 APK 标签 68。没有执行 CPS/安装器/APK；ARM 原厂函数只在明确的人工外设模型上执行。

### 集成测试与构建

在设置 `K10_REFERENCE_FIRMWARE` 与 `K10_REFERENCE_RAW` 后，`make test` 的 **91 个 discover 测试及另外 2 个驱动测试全部通过，无跳过**。

| 测试组 | 数量 | 内容 |
|---|---:|---|
| 原有容器/镜像/HAL | 23 | 首版测试保留，含官方样本逐字节回环 |
| 信道 codec | 13 | 字段、名称编码、亚音、未知字节保留、属性删除、VFO/扫描及可选原指令实验 |
| CPU 仿真底座 | 7 | 独立 Thumb 程序、hook、未建模 MMIO 拒绝、写保护、执行权限、指令上限、NOR 约束及原厂实验 |
| 编程协议 | 31 | 30 项合成测试，另 1 项原厂指令重放；覆盖 CPS 固定 128 B 数组和 count/padding 关系 |
| APK schema 摘要 | 17 | 字节/位单位、基址覆盖、嵌套列表、重叠、版本比较、重复键和资产大小边界 |
| C 驱动测试 | 另 2 | 首版的 17 个场景及 UBSan |

普通 CI 安装 `requirements-re.txt`，但不下载厂商样本：91 项中 4 项明确跳过，另外 2 个 C 驱动测试照常执行。官方应用和固件文件不进入 Git、CI artifact 或本仓库的测试数据。

用原工具链再次运行 `make -j2 package` 成功，诊断固件 raw 仍为 4,724 B，raw 与更新容器 SHA-256 均与上方首版结果相同。本轮添加的是研究工具、模型、测试和文档，没有把未验证的完整通信/射频功能接入裸机程序。

### 原指令与静态证据

| 证据组 | 本次执行/检查 |
|---|---|
| 信道 | 11 保存案例、6 装载案例、18 亚音边界、8 TX 算术案例、29 个原厂菜单显示值；另含删除、属性缓存、有效性及扫描列表 |
| 协议 | 42 案例：1 CRC、8 parser、27 parser→dispatcher→sender、6 sender |
| 协议到 NOR 的完整回环 | 6 事务；实际 RMW 执行；记录和属性读回一致；整个合成 NOR 的邻居保持不变；门控 ACK/零回应不触发 NOR 操作 |
| NOR | 8 案例，覆盖模式差异、页/扇区边界和缓存 |
| RF GPIO | 7 案例，覆盖读写位序、输入/输出方向及 32 位寄存器对拆分 |
| CPS | SHA 固定的 .NET 主程序；9 个结构与 12 个方法的元数据/IL 静态交叉检查 |
| APK | 27 张表、242 个字段声明；两份 K8 schema 比较；228 个字段声明存储映射 |

以下五份 JSON 均由对应脚本重新生成，并与仓库记录做完整结构相等检查，通过：

- [channel-layout.json](evidence/channel-layout.json)
- [programming-protocol.json](evidence/programming-protocol.json)
- [storage-emulation.json](evidence/storage-emulation.json)
- [rf-bus-emulation.json](evidence/rf-bus-emulation.json)
- [cps-protocol-crosscheck.json](evidence/cps-protocol-crosscheck.json)

CPU 仿真入口与 CPS 静态检查入口都拒绝 Python `-O`，避免关闭断言后生成无效的通过报告；实际验证过退出码为 1。文档本地链接和证据 JSON 格式检查通过。

模型的 2 MiB 外部 NOR、人工 session、波段使能与虚拟输入 RAM 都是实验条件。没有物理设备枚举、真实 flash 持久化、USB 抓包、板级波形或 RF 收发测试。模型和证据边界详见 [emulation.md](emulation.md)。
