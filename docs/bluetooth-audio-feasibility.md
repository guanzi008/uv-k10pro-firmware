# UV-K10 Pro 蓝牙耳机音频可行性：官方固件与 PHY6235SC

更新：2026-11-28（北京时间）。对象为用户确认装有 **PHY6235SC** 的 UV-K10 Pro，以及固定官方主 MCU 样本 **K10PRO_ENG_v8.02.16**。这是研究结果，没有实现或实机验证蓝牙耳机音频。

## 1. 当前结论

**没有找到可通过修改当前主 MCU 固件，直接开启普通蓝牙耳机音频的现成路径。** 已恢复的是 K8 私有 BLE 写频、状态、位置和 APRS 数据链。PHY6235 厂家资料将其定义为 BLE / 私有 2.4 GHz SoC，没有给出 Classic BR/EDR、A2DP 或 HFP 支持；LE Audio 需要的 ISO 和对应 SDK 也尚未得到该型号的正面支持证据。[S1][S2][S3][S4]

本轮有一个积极的新发现：**主 MCU 中存在 ADC + DMA 采样，并且其解调结果确实能够进入蓝牙相关数据上传函数。** 目前上传的是经校验的 APRS 数据包，不能把这条链说成已有语音传输。它为后续自定义 BLE 压缩语音实验提供了可研究的采样入口；实际模拟信号来源、所有接收模式的音频接入和链路吞吐仍待实机确认。

| 目标 | 当前工程判断 | 尚缺条件 |
| --- | --- | --- |
| 官方 App / Web 读写频、位置、APRS 数据 | 已有软件与协议证据 | 对应接口、真实设备与时序验收 |
| 原机仅刷主 MCU 固件，直连经典蓝牙耳机听音 | 当前不能承诺实现 | BR/EDR 控制器与 A2DP Source 等能力没有依据 |
| 普通耳机听音并回传麦克风 | 当前不能承诺实现 | 还需 HFP Audio Gateway 或兼容的双向 LE Audio、回传音频路由及 PTT 控制 |
| 自定义 BLE 语音 → 专用手机 App / 自制接收端 | 有研究价值，尚未实现 | 采样来源、压缩、实时排程、BLE 吞吐、接收端播放 |
| 标准 LE Audio → 支持 LE Audio 的耳机 | 未证实 | PHY6235 的 ISO/CIS、可用协议栈和 LC3 资源依据 |
| 新增具备所需音频角色的模块 | 协议方向成立的硬件方案 | 电平与音频接点、供电、模块角色及耳机互操作验证 |

## 2. 样本和证据边界

- 主 MCU 解码镜像长度：118,956 字节；加载基址：`0x08002000`。
- SHA-256：`061759672cc69a1b00420ce6fae67b0756d3db111f8fa4329cadc940dcd17f17`。
- 本轮重新检查该镜像的 ARM Thumb 指令、常量和调用关系；不是执行 PHY6235SC 的 RISC-V 程序。
- 尚未取得本机 PHY6235SC 内部固件、可用 SDK、实际烧录入口或蓝牙空口抓包。
- APK 证据复核自 Web 仓库固定提交 `8840949154fdb4766dac36fb132b0d0c8465c293`；本轮没有重新取得完整 APK AOT 工作目录。这里只采用具有 K8 / UV-K10 Pro 绑定的路径。[S5][S6]
- 本轮 ADC、DMA、串口和音频判断是静态证据及明确假设下的计算，没有新增实机测量或完整硬件仿真。

复核工具为 [verify_ble_audio_static.py](../tools/verify_ble_audio_static.py)，配套 [53 组字节锚点](evidence/ble-audio-static-anchors.json)及[本轮实际运行结果](evidence/ble-audio-static-result.json)。运行方式：

```bash
python3 tools/verify_ble_audio_static.py --sample /path/to/K10PRO_ENG_v8.02.16_analysis_only.raw.bin --output /tmp/ble-audio-static-result.json
```

该工具只核对固定样本及关键字节，不会自动证明注释中的软件语义、PCB 走线或无线功能。厂家手册来源、摘要与芯片引脚见 [bluetooth-hardware.md](bluetooth-hardware.md)。

## 3. 现有蓝牙链到底在做什么

### 3.1 手机连接电台的私有 GATT 服务

官方 APK 的型号绑定为 `K8RadioProtocol.gattProfile → BleGattProfile.k8 → UV-K10 Pro`。APK 68 中关键调用为 `0xBE82B8 → 0xBE82D0`。

| 项目 | 已恢复内容 |
| --- | --- |
| 服务 | `0000ff12-0000-1000-8000-00805f9b34fb` |
| 手机发送特征 | `0000fff2-0000-1000-8000-00805f9b34fb`，withResponse |
| 手机首选接收特征 | `0000fff3-0000-1000-8000-00805f9b34fb`，notify / indicate |
| 应用协议 | 私有 `AB CD … command … payload … CRC … DC BA` 帧 |
| K8 App 发送策略 | 完整帧一次提交；帧后等待 120 ms |

App 中的扫描是手机寻找电台，并不证明电台能够扫描、选择和连接耳机。120 ms 是已恢复的 App 发送策略，不能当作 BLE 硬件的固有吞吐上限，也不能直接用于连续音频推流。[S5]

### 3.2 主 MCU 的串口、状态与命令

本轮在 `0x08018B4E..0x08018B52` 重新确认初始化代码：`225 << 9 = 115200`，作为参数传给 `0x0801A994`。后者配置外设基址 `0x40004800` 的 USART3。接收 DMA 缓冲为 `0x200022F8`、200 字节；发送函数 `0x0801AAF0` 使用私有帧和 CRC-16/X25。

这是代码中的配置值。USART3/PB10/PB11 与 PHY6235SC 的板级连通尚未测定；不把串口配置直接改写成已测得的蓝牙芯片引脚。

| 命令 / 函数 | 已确认的行为 | 对耳机音频的意义 |
| --- | --- | --- |
| `0x02C3 / 0x02C5` | 外置 NOR 读 / 写 | 配置数据，不是音频 |
| `0x02DD` | RF/APRS 数据包发送 | 不能当成语音发送命令 |
| `0x02E1`，`0x08003644` | 接收 APRS 包的异步上传 | 上传解调后的数据，不是 PCM |
| `0x0320`，`0x08002C20` | 拷贝 16 字节与 6 字节字段；回复 `0x0321` | 模块信息交换；字段语义未完整确认 |
| `0x0322 / 0x0323` | 更新状态 `0x20000162` | 连接状态不等于音频协商 |

APRS 上传函数检查 `0x200023FC == 1` 和 `0x20000162 == 3`。不要把六字节字段仅凭长度命名为蓝牙地址，也不要把十六字节字段擅自命名为模块固件版本。

开关路径 `0x0800DE24 → 0x08005610` 调用 `0x08018994 / 0x080189B0`，切换 PB10 的 AF 输出及模块状态。它还通过 `0x08017FC4(index=5, value=1/0)` 修改主 RF 缓存寄存器 `0x33` 的 `0x0002` 位。该软件控制可见，但尚无走线证据把这个位命名为 PHY6235SC 的供电或复位信号。

没有在已恢复链中识别出耳机列表、音频配对、A2DP/HFP 协商或 ISO 音频接口。该范围性结论不是对未取得的 PHY6235SC 内部程序做全功能否定。

## 4. 新发现：ADC 到 APRS 上传的实际代码链

| 阶段 | 地址 / 数据 | 静态观察 |
| --- | --- | --- |
| ADC 采样缓冲 | `0x20002160`，192 个 16 位样点 | 共 384 字节，分为两半 |
| DMA 中断 | `0x08017184` | 分别把 `0x20002160`、`0x20002220` 交给处理函数 |
| 样点处理 | `0x08004904`，每次 96 点 | 正交表混频、41-tap 滤波与位判决 |
| 帧处理 | `0x0800420C` | `0x7E` 标志、去位填充、组字节、CRC/FCS 检查 |
| 完整包待处理 | `0x20000322` | 通过校验后置位 |
| 主循环交接 | `0x0800D092..0x0800D098` | 根据标志调用 `0x08004CA0` |
| 上传调用 | `0x08004CC0 → 0x08003644` | `0x02E1` 最多带 128 字节的已解调包，经 transport 2 发送 |
| 协议解释 | `0x080046E8` | 处理七字节地址块及 APRS 信息类型 |

“16 位样点”在此描述 DMA / 缓冲的存储宽度，并未认定 ADC 有效分辨率为 16 bit。

这些行为共同支持 **AFSK / HDLC / AX.25 / APRS 接收链** 的解释。它比搜索 `audio` 或 `bluetooth` 字符串更有判断力：这里确实存在与音频采样有关的数据处理，但进入串口的是解调后的协议字节。

### 4.1 采样输入已定位到 ADC 通道 9 / PB1

`0x080173BC..0x080173CC` 对 GPIOB 的 pin mask 1 和 2 设置模式 3，即 PB0/PB1 均为模拟模式。进入 DMA 时，`0x080174AC..0x080174B4` 把 `0x05B00009` 交给序列配置帮助函数；退出时，`0x0801755C..0x08017564` 改传 `0x05800008`。帮助函数 `0x0801AD70..0x0801AD78` 确实提取低 5 bit 写入序列槽，因此分别是通道 9 和 8，不是只看常量末尾猜测。

普冉 **PY32F071 数据手册 v1.4，p39** 明确 `PB1 = ADC_IN9`、`PB0 = ADC_IN8`。[S12] 两类证据合起来，可以将 AFSK 采样的 **MCU 输入定位到 PB1**；仍不能指定 BK4829 的哪个输出脚连接它，也未确认 Si4732/BK1080 是否接入这个节点。未确认本机 MCU 封装，因此这里只记端口名，不套物理脚号。

普通 ADC 读取 `0x0801735C` 在 DMA 模式下返回 `0x20000076` 中缓存的值；非 DMA 模式才发起转换。进入 DMA 前先刷新缓存，退出时改回通道 8。主循环 `0x0800CB18..0x0800CB2A` 将普通读取结果交给电量/电压处理链。因此可区分 **电量采样通道 8** 与 **AFSK 通道 9**，但 PB0 外接电池分压的具体电阻与走线还没有测量。

### 4.2 采样率的条件推算

定时器配置函数 `0x08017590` 对基址 `0x40014000` 写入 `PSC=1`、`ARR=2499`，并配置更新事件作为触发输出；ADC / DMA / 计数器启用位于 `0x080174F2..0x0801750A`。

**如果该定时器输入时钟为 48 MHz**，则：

```text
fs = 48,000,000 / (1 + 1) / (2499 + 1) = 9,600 samples/s
96 samples / 9,600 samples/s = 10 ms
```

48 MHz 在这里是计算前提，不是本轮实测时钟；9.6 kHz 不应被写成无条件的实测采样率。定时器地址不套用其他 MCU 的 TIM 编号。

### 4.3 它给自定义语音带来了什么

已经有采样、分块中断和数字处理入口，因此可以研究在合适模式下把样点送给压缩器。但仍需证明：

1. 该 ADC 物理输入来自哪个接收音频节点，电平、带宽和偏置是什么。
2. 普通语音接收状态是否保持该输入有效；Si4732 和 BK1080 的音频是否也能到这里。
3. 停用或复用 APRS 解调时会改变哪些模式，CPU 实时预算和缓冲是否足够。
4. 模块当前程序能否稳定转发持续通知，以及丢包、时钟漂移与重连如何处理。

自定义语音需要独立的数据类型、序号、采样时基和播放缓冲。不能把 `0x02E1` 的 APRS 包伪装成 PCM，也不能只修改 App 名称或 GATT UUID 就获得标准耳机兼容性。

## 5. 带宽：有压缩语音空间，不等于已经能播音

按当前代码中的 115200 配置，并**假设串口采用 8N1**，每字节占 10 位，理论数据上限为 11,520 字节/秒，尚未扣除应用帧头、CRC、调度和流控开销。

| 假设的单声道格式 | 纯音频数据率 | 与 115200、8N1 的关系 |
| --- | ---: | --- |
| 8 kHz × 16-bit PCM | 16,000 B/s | 超过理论串口字节率 |
| 9.6 kHz × 16-bit PCM | 19,200 B/s | 超过理论串口字节率 |
| 8 kHz × 4-bit ADPCM | 4,000 B/s | 纸面有余量，未实现/测量 |
| 9.6 kHz × 4-bit ADPCM | 4,800 B/s | 纸面有余量，未实现/测量 |

此处 ADPCM 是设计计算示例，不表示 stock 已包含该编码器；数据率也不包含分块状态等额外信息。

PHY6235 厂家手册 §4.7 给出的 UART 上限为 1 Mbps，说明 115200 不是该芯片家族的绝对硬件上限。但提高速率要两端固件、实际连接和信号完整性都支持，不能只改单侧配置。BLE 1M/2M 是空口 PHY 速率，与应用有效吞吐、串口速率是三个不同指标。[S1][S2]

## 6. 三种蓝牙音频路线必须分开

### 6.1 经典蓝牙耳机

采用 A2DP 向耳机播放时，电台应具备 **A2DP Source**，耳机是 Sink；需要麦克风回传，则通常需要电台具备 **HFP Audio Gateway**、耳机作为 Hands-Free。HFP 的下行也可用于仅收听语音；A2DP 不是所有单向听音方案的唯一选择。A2DP/HFP 使用经典蓝牙音频体系，不能通过在 BLE GATT 中写入音频字节来替代。[S3][S7][S8]

PHY6235 手册说明的是 BLE / 私有 2.4 GHz FSK 能力，没有 BR/EDR 或上述角色的实现依据。因此，**在现有主 MCU 固件里补几个配置命令，不足以得到普通耳机音频**。要改变这个判断，首先需要精确芯片的相关控制器和栈能力证据，而不是另一颗 PHY62xx 的例程。

### 6.2 标准 LE Audio

BLE 5.4 不自动包含 LE Audio。标准路线需要 LE Isochronous Channels、LC3，以及 BAP 等音频配置与服务。[S4][S9]

连接耳机的一般单播场景涉及 Unicast Client 配置 CIG/CIS；广播音频使用 BIG/BIS，不能代替耳机麦克风回传。耳机自身也必须支持对应 LE Audio 功能。[S10]

本次没有找到 PHY6235 的 ISO/CIS/LE Audio 适配 SDK。资料中的 8 KB SRAM 会限制协议、编解码和缓冲预算，但不能单凭 RAM 大小作数学上的绝对不可能结论。当前状态是**未证实，不能作为可交付功能**。

### 6.3 自定义 BLE 音频

BLE 数据链可以承载自定义压缩样本，由专用 App 解码播放；手机再把播放音频路由到它已经连接的耳机。这与电台直接配对普通耳机不同，会增加手机依赖、排队延迟和系统音频路由问题，麦克风回传也需要单独实现。

如果只研究原硬件扩展，最有依据的实验对象是 **ADC 分块 → 压缩语音 → 独立 BLE 数据类型 → 专用 App**。是否能不改板就完成，仍由实际 ADC 线路和模块吞吐决定。

## 7. 硬件补充方案的必要条件

如果采用 A2DP 让现有普通耳机播放广播或对讲接收音频，独立模块必须支持 **A2DP Source / 音频发送端**；只支持 A2DP Sink 的常见音频接收板角色相反。[S7]

要使用耳机麦克风，还要确认模块具备 HFP AG 等正确角色、到电台发射音频输入的线路，以及按住/松开或其他明确的 PTT 控制。耳机的通话按钮不会自动成为对讲机 PTT。音频接口的电平、差分/单端和功放前后位置需要先定位；目前没有为这块板指定焊点。

## 8. 下一轮最有判断力的验证

1. **PCB 连通与采样源**：核对 USART3 与 PHY6235SC 的实际连接；定位主 MCU ADC 输入与主 RF、Si4732、BK1080 音频切换关系。
2. **模块能力**：取得本机 PHY6235SC 程序身份与适配 SDK；直接确认是否支持 ISO，而非只问蓝牙版本号。
3. **单向数据实验**：先在纯接收场景验证样点导出、压缩、缓冲和连续 BLE 通知，不声称兼容耳机。
4. **端到端验收**：使用专用接收 App 测量音质、延迟、丢包和长时间稳定性；若走标准音频模块，再验证实际耳机的发现、配对及播放。

## 来源

- **[S1]** [PHY6235 官方产品页](https://www.phyplusinc.com/product_detail/17.html)。
- **[S2]** [厂家原文 PHY6235 Product Specification v1.0，由代理商公开分发](https://www.dnsj88.com/filedownload/2972840)，修订表 2024.5，文件元数据 2024-08-11；SHA-256 `3fce0054ed8fd72981ec0dc39cb762333ce34d8796a703e367b2da1d52c7cb70`。不是官网文件名为 20240926、此前返回 403 的那一份。页码按正文标号。
- **[S3]** [Bluetooth SIG：Communicating supported Bluetooth functionality](https://www.bluetooth.com/communicating-supported-bluetooth-functionality/)，Classic Audio、A2DP/HFP 与版本号说明。
- **[S4]** [Bluetooth SIG：LE Audio FAQs](https://www.bluetooth.com/media/le-audio/le-audio-faqs/)。
- **[S5]** [K8 GATT 与 APK 调用链](https://github.com/guanzi008/uv-k10pro-web/blob/8840949154fdb4766dac36fb132b0d0c8465c293/docs/ble-profile.md)。
- **[S6]** [K8 实时位置 / APRS 证据](https://github.com/guanzi008/uv-k10pro-web/blob/8840949154fdb4766dac36fb132b0d0c8465c293/docs/live-functions.md)。
- **[S7]** [Bluetooth SIG：A2DP v1.4.1](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/a2dp_v1-4-1_1752513648/A2DP_v1.4.1/out/en/index-en.html)，Source/Sink 角色。
- **[S8]** [Bluetooth SIG：HFP v1.10 变更标记版](https://docs.bluetooth.com/download/hfp_v1-10_showing_changes_since_hfp_v1-9/?tmstv=1765934392)，§2.2 AG/HF 角色。
- **[S9]** [Bluetooth SIG：LE Audio specifications](https://www.bluetooth.com/learn-about-bluetooth/feature-enhancements/le-audio/le-audio-specifications/)。
- **[S10]** [Bluetooth SIG：Basic Audio Profile v1.0.1](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/16212-BAP-html5/out/en/index-en.html)，§2.2、§3.4、§4、§5。
- **[S11]** [Phyplus 官方 SDK 页](https://en.phyplusinc.com/support/5.html)，公开 3.x 对应 PHY6222/PHY6252，2.x 对应 PHY6212，不能混用为 PHY6235 证据。
- **[S12]** [普冉 PY32F071 系列数据手册 v1.4](https://www.puyasemi.com/download_path/%E6%95%B0%E6%8D%AE%E6%89%8B%E5%86%8C/MCU%20%E5%BE%AE%E5%A4%84%E7%90%86%E5%99%A8/PY32F071%E7%B3%BB%E5%88%97%E6%95%B0%E6%8D%AE%E6%89%8B%E5%86%8C_V1.4.pdf)，p39，PB0/ADC_IN8、PB1/ADC_IN9。
