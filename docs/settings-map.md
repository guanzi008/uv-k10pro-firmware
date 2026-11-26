# 官方应用声明的设置与功能存储地图

本页根据两个静态 APK 中的 `assets/flutter_assets/assets/K8.json` 独立整理。
**除明确列出的交叉核验项外，地址、长度、选项与类型均属于应用 schema 的声明，不等同于已经执行验证的固件行为。**
未运行 APK、CPS 或安装器，未读写实机。完整厂商 JSON 不随仓库发布。
机器可读的事实摘要见 [vendor-schema.json](evidence/vendor-schema.json)，来源清单见
[hardware-sources.json](evidence/hardware-sources.json)。

## 1. 样本身份与证据范围

| 样本标签 | 分发来源 | APK SHA-256 | K8.json 大小 / SHA-256 |
| --- | --- | --- | --- |
| `64` | [泉盛官方 CMS 11398][official-apk]，下载文件名含 `1.1.0-64` | `b12adceddfe49c7df04eab3b8b6dbc336d7a6eb32a0df62bc75b7e978559d048` | 175,298 B；`5df044c86a2a2eee8fd4b709bf47c1a6cfeb030b30d23c3010b09f19593dfc5f` |
| `68` | [SHJ 经销商资源包][shj]，内含文件名含 `1.1.0-68` 的 APK | `0395fa0332d025da73a39aad49b01eaedfe59e8296f17df56fa937ff9b83b326` | 175,876 B；`7b8cb1c712251fd892101f3650571ecf8992d285f82b9518c730402e9909a2a5` |

这里的 `64` / `68` 是分发文件名标签，未当作 Android manifest 的版本号认证。
两个资产均声明 `deviceModel=K8`、`addOffset=0`。官网政策也明确将 UV-K10 Pro 标为协议机型 K8。
`products/data.json` 中的产品目录只有 UV-K10 Pro；这不证明 APK 其余代码没有其他机型支持。

### 与已分析固件的交叉核验

| 内容 | 当前证据等级 |
| --- | --- |
| 用户信道表从 `0x00000` 起、记录步长 64 B；VFO 区从 `0x10000` 起 | 与真实固件信道读写函数和 [channel-storage.md](channel-storage.md) 交叉核验 |
| APK 用户信道上限 999、VFO 声明 14 条 | 还与官方 999 信道说明相容；不表示 NOR 中只有 999 个可被底层寻址的槽位 |
| 主机 Flash 读写命令可访问这些区域 | 仅证明传输路径；不证明每个设置字段的实际含义，见 [programming-protocol.md](programming-protocol.md) |
| APRS、DTMF、5TONE、扫描列表及广播接收设置字段 | 本页的偏移来自 APK 声明；仍需逐项对照固件函数或观测原厂写入 |

## 2. 地址与单位的计算规则

普通字段的声明地址按 `addOffset + (字段 addr 或所在表 addr) + offset` 计算。
字段自己的 `addr` **覆盖** 表基址，不再把两个地址相加。当前 `addOffset` 为零。
列表第 `i` 条记录再加 `i × byteSize`；本页的 `i` 均从零开始。

| schema 形式 | offset / length 单位 | 本工具如何处理 |
| --- | --- | --- |
| 普通 `group` 或 `list` 字段 | 字节 | 输出字节地址、字节长度；列表地址表示第 0 条记录 |
| `type=object` 且有 `bitSize` | 位 | 输出所在字节和声明的位偏移，不把 `offset=7` 解释为第 7 字节 |
| `type=empty` 或 `isStatic` | 无存储字段 | 标为 UI-only，不虚构地址 |
| 没有 offset 或 length 的字段 | 未完整声明 | 标为 unmapped，不填默认偏移 |
| `valueReadFormat` / `valueWriteFormat` | 转换函数标识 | 保留名称，不执行、不推定字节序和缩放 |

`type=int` 不足以证明是无符号小端数；标签中的 `[ms]` 或 `(MHz)` 也不足以证明原始整数就以该单位存储。
摘要工具把显示单位与存储单位分开：未验证的存储单位为 `unresolved`。
`k8FrequencyRead` 的名字可定位后续代码，但不能单靠名字认定所有引用处都已经验证为 10 Hz 单位。

## 3. 主要存储区域

下表的区间使用不含尾地址的写法。它们是 schema 所声明的布局或列表容量，不是擦除分区表。

| 区域 | 声明的基址 / 跨度 | 记录或用途 |
| --- | --- | --- |
| 用户信道 | `0x00000`，64 B × 999，尾地址 `0x0F9C0` | `channel_list` |
| VFO | `0x10000`，64 B × 14，尾地址 `0x10380` | `vfo` |
| 信道辅助标志 | `0x11000`，999 B | `channel_list.flag`，单独的 formatter |
| 收音机设置 | `0x12000` 的多个偏移 | FM / LW / MW / SW，见下文 |
| 通用 / 按键 / 信令设置 | 主要在 `0x13000` | 多个逻辑分组共用这个区域 |
| 设备名及部分信令码 | `0x14000` 的多个偏移 | 设备名、DTMF / 5TONE 代码 |
| A/B 显示选择 | `0x15000` 与 `0x15002`，各 2 B | startup_settings 的字段 |
| 扫描列表 | `0x16000`，128 B × 32，尾地址 `0x17000` | 每列表声明最多 48 个 2 B 成员 |
| 开机密码 | `0x17000`，4 B | 自定义读写转换，不能当明文整数 |
| 隐藏参数 | `0x19000` 的多个偏移 | 仅记录声明，不提供解锁或默认写入 |
| VFO 频段设置 | `0x19010`，声明 144 B | 自定义 `band` 类型，内部格式尚未恢复 |
| DTMF 联系人 | `0x1A000`，24 B × 16，尾地址 `0x1A180` | 名称和代码 |
| 5TONE 联系人 | `0x1A800`，24 B × 16，尾地址 `0x1A980` | 原始表 ID 拼作 `toenLinkman` |
| 频段组 | `0x1C000`，272 B × 8，尾地址 `0x1C880` | 组使能、名称、自定义频段 buffer |
| APRS | `0x1E000`，本次已声明字段落在前 256 B | 位置、呼号、路径、备注、频率等 |
| 预制消息 | `0x2E000`，256 B × 64，尾地址 `0x32000` | 每条仅部分字节有 schema 字段 |
| 开机图 | `0x45000`，1,032 B | 原始 buffer；不能单靠长度判定屏幕控制器或分辨率 |

声明的列表长度和空洞不是“全部可覆写空间”。任何后续编辑器都应先保留未理解字节，尤其不要把整个表的未声明区域清零。

## 4. 通用、按键、开机与显示

以下偏移以 `0x13000` 为基址；除特别注明外长度为 1 B。

| offset | 字段含义 / schema key | 备注 |
| ---: | --- | --- |
| `0x00` | 主信道 `mainChannel` | 应用枚举 |
| `0x01` | NOAA 静噪 `noiseFloor` | 应用 0–9 选项 |
| `0x02` | 发射限时 `general_settings.gen_freq_meter_tot` | 标签为分钟，存储缩放未验证 |
| `0x03` | NOAA 搜索 `noaaSearch` | 应用枚举 |
| `0x04` | 按键锁 `keyLock` | 枚举类型，不假定 bool |
| `0x05/06/07` | VOX 开关 / 等级 / 麦克风设置 | 三个相邻字段 |
| `0x08/09` | VFO 模式 / 信道显示方式 | 显示选项在两版 APK 之间有变化 |
| `0x0B/0C/0D` | 省电 / 双守候 / 自动背光 | 各自具有不同类型或枚举 |
| `0x0E` | 即呼信道 `call_channel`，2 B | 读 `plusOne`、写 `reductionOneNoBuffer`；索引转换不能忽略 |
| `0x10` | 按键提示音 `beep` | bool 声明 |
| `0x11..14` | 侧键 1、2 的短按 / 长按 | 共 4 个独立字节 |
| `0x15/16/17/18` | 扫描恢复 / 自动锁 / 开机界面 / 报警模式 | 应用枚举 |
| `0x19` | 通话结束提示 `end_talk` | `64` 包为 bool，`68` 包改为三选项 int |
| `0x1A/1B/1C/1D` | 中继尾音 / 尾音消除 / 蓝牙 / 呼叫许可音 | `call_permit_tone` 的翻译 key 在新版修正 |
| `0x1E` | 时区 `timeZone`，2 B | 不由字段宽度猜测时区单位 |
| `0x20/21/23` | 收音机模式 / 背光收发提示 / 语言 | 来自不同逻辑分组 |
| `0x24/25/26` | 红 / 绿 / 蓝背光亮度 | 支持“有独立 RGB 背光配置”的软件判断，不证明彩色像素面板 |
| `0x27/28/29` | GPS 开关 / SBar / 界面复位 | `brightness` 的 UI 标签实际指复位时间，key 名不可靠 |
| `0x2A/2B/2C` | 遥毙相关开关 / 信令侧音 / 解码响应 | 只记录地址，未推定运行行为 |
| `0x2D` | 对频超时 `gen_freq.gen_freq_meter_tot` | 与另一个同名 key 的字段不同，必须保留完整分组路径 |
| `0x2E/2F` | 侧键 3 短按 / 长按 | 各 1 B |
| `0x32` | CW 频偏 `cwFrequencyOffset`，2 B | 原始单位未确认 |
| `0xF8 / 0x108 / 0x118` | 开机字符串 1 / 2 / BLE 名称 | 各 16 B；均调用 GB2312 名称 formatter |

设备名称另在 `0x14060`，长度 16 B。A/B 显示项位于 `0x15000` 与 `0x15002`，各 2 B。
开机密码位于 `0x17000`，长度 4 B，应用明确使用专门的密码 formatter，本文不把它解释为明文或一般整数字段。

## 5. APRS 参数与已发现的重叠

下表基址为 **`0x1E000`**。长度均为字节，唯一例外是之后单独解释的 filter 对象。

| offset | 长度 | schema 字段 / 含义 |
| ---: | ---: | --- |
| `0x03` | 2 | `transmit_interval`，发送间隔声明 |
| `0x04` | 1 | `ack_id`，**与上项重叠** |
| `0x05` | 1 | `beacon_source`，信标来源 |
| `0x06` | 1 | `icon_id`，图标枚举 |
| `0x07` | 1 | filter 对象，包含 8 个 bit 字段 |
| `0x08/09/0A/0B` | 各 1 | 测试音 / 带宽 / 亚音类型 / 功率 |
| `0x0C` | 2 | `preCarrierTime`，预载波 |
| `0x0E/0F` | 各 1 | 自身 / 目标 SSID |
| `0x10` | 4 | `launchSimulatedSubAudibleValue` |
| `0x14/18/1C` | 各 4 | 高度 / 纬度 / 经度；编码和缩放仍未确认 |
| `0x20/21` | 各 1 | 南北纬 / 东西经标志 |
| `0x22` | 2 | `timing_send`，定时发送 |
| `0x24/25/26` | 各 1 | 信标格式 / 自动回复 / 自动回复间隔 |
| `0x28/30` | 各 8 | 自身 / 目标呼号，名称 formatter |
| `0x38` | 56 | `APRSPath`，路径 |
| `0x70` | 56 | `APRSText`，位置备注 |
| `0xA8..C4` | 每项 4 | 8 个频率字段，步长 4 B，均声明 `k8FrequencyRead/Write` |
| `0xC8` | 56 | `autoReplyText`，自动回复文本 |

### filter_setting 的单位是位

此表声明 `addr=0x1E007`、`type=object`、`byteSize=1`、`bitSize=8`。
`position/mic_e/object/item/message/wx/nmea/status` 的 offset 分别为 `7/6/5/4/3/2/1/0`，length 都为 1。
它们全部位于 **同一个字节**，不是 `0x1E007..0x1E00E` 的 8 个开关。
应用另指定 `filterSettingRead/Write`；具体打包位序和保留策略仍需要重放转换函数或观测写入，摘要工具不生成写掩码。

### 不能悄悄修正的 schema 冲突

两版都有 `transmit_interval=[0x1E003,0x1E005)` 与 `ack_id=[0x1E004,0x1E005)` 的重叠。
目前不能确定是别名、特殊转换还是 schema 错误。工具把它作为 `declared-overlap` 输出。
不能自行把间隔长度改成 1 B，再将修改后的推测描述为逆向事实。

## 6. DTMF 与 5TONE

两类设置交错分布，不能把整个 `0x13000` 区简单切成两个连续块。以下 offset 的基址为 `0x13000`。

| 用途 | DTMF offset / 长度 | 5TONE offset / 长度 |
| --- | --- | --- |
| 本机 ID | `0x78` / 8 B | `0x80` / 8 B |
| 分隔符 / 重复音 | `0x40` / 2 B | `0x50` / 2 B |
| 组码 | `0x60` / 2 B | `0x62` / 2 B |
| 自动复位 | `0x64` / 1 B | `0x65` / 1 B |
| 预载波 | `0x68` / 2 B | `0x6A` / 2 B |
| 首码持续 | `0x6C` / 2 B | `0x6E` / 2 B |
| 单码持续 | `0x70` / 2 B | `0x72` / 2 B |
| 码间隔 | `0x74` / 2 B | `0x76` / 2 B |
| 上线码 | `0x88` / 16 B | `0x98` / 16 B |
| 下线码 | `0xA8` / 16 B | `0xB8` / 16 B |

DTMF 的 `*#` 持续时间另在 `+0x42`，2 B；5TONE 协议标准在 `+0x52`，1 B，
14 个用户自定义频率项在 `+0xD8..+0xF2`，每项 2 B。
部分 UI 标签注明 ms 或 s，原始计数单位和有效范围尚需代码验证。

代码字符串读取声明 `asciiValueRead`，写入分别声明 `asciiValueWrite8/16`。
这些是函数标识，不保证名字中的 8/16 就是字段的实写长度。
例如部分声明长度为 8 B 的字段也引用 `asciiValueWrite16`，在恢复 formatter 前不能根据命名生成写入。

另外两组代码在 `0x14000`：DTMF 的字段偏移为 `0x30/0x40`，5TONE 为 `0x38/0x48`，各声明 8 B。
联系人表分别在 `0x1A000` 与 `0x1A800`，均为 16 条 × 24 B；单条 `+0` 为 16 B 名称，`+16` 为 8 B 代码。
这是存储声明，未验证远程指令的认证、触发与响应流程。

## 7. 扫描列表、预制消息与频段组

### 扫描列表

列表 `i` 位于 `0x16000 + i×128`，`0≤i<32`。

| 单条 offset | 长度 | 声明 |
| ---: | ---: | --- |
| `0` | 16 B | 名称 |
| `16` | 2 B | 优先信道 1 |
| `18` | 2 B | 优先信道 2 |
| `24` | 96 B | 48 项 × 2 B 的成员区，调用 `k8ScanChannelGroupRead/Write` |

`20..23` 及 `120..127` 没有这里可直接映射的字段；不能假定全部保留字节必须为零。
UI 有 `countFieldKey=count` 的引用，但该表没有一个完整声明的 `count` 存储字段，不能仅据名字猜它在 `+20`。
成员区存在专门 formatter，也不能直接断定空值、用户编号与零基索引的编码。

### 预制消息

第 `i` 条位于 `0x2E000+i×256`，`0≤i<64`。`autoIndex` 声明占 `+0..3`，消息文本占 `+24..79`，56 B。
名称 formatter 与用户界面字符数限制不是同一概念；剩余 196 B 没有由这两个字段解释。

### 频段组

第 `i` 组位于 `0x1C000+i×272`，共 8 组。使能字段 `+0` 占 4 B、名称 `+8` 占 8 B、
频段 buffer `+16` 占 144 B。使能和 buffer 都声明自定义转换器。
`vfo_frequency_band_design` 另在 `0x19010` 声明 144 B 的 band 对象。相同长度提示可交叉比较，不能直接证明内部格式完全相同。

## 8. FM / LW / MW / SW 收音机设置

这四组均通过各字段的 `addr=0x12000` 和 offset 定位。

| 模式 | 控制字段起点 | 频率 buffer 起点 | buffer 声明长度 | UI 信道选项 |
| --- | --- | --- | ---: | --- |
| FM | `0x12100` | `0x12108` | 264 B | 1–32 |
| LW | `0x12300` | `0x12308` | 264 B | 1–32 |
| MW | `0x12500` | `0x12508` | 264 B | 1–32 |
| SW | `0x12700` | `0x12708` | 1,032 B | 1–128 |

FM 控制区的 `+0/+1/+2/+3` 分别声明工作模式、信道、AGC、步进。
LW/MW/SW 的 `+0/+1/+2` 为模式、信道、AGC，`+4/+5/+6/+7` 为 AM 步进、LSB/USB 步进、AM 带宽、LSB/USB 带宽。

这些 buffer 使用应用自定义控件，不是 schema 中展开的固定记录列表。
虽然 `264=8+32×8`、`1032=8+128×8` 在算术上成立，**本轮没有据此认定真实格式就是 8 B 头加 8 B 记录**。
官网的 SW 标称数量为 18，而此 schema 的 UI 枚举为 1–128；两者必须保留来源区别，不能任选一个当作实机存储上限。

## 9. 两个 APK 的静态差异

对整个 JSON 做结构比较后，以下差异之外未发现其他结构变化。摘要工具只比较保留的布局 / 类型 / 转换器 / 枚举摘要，不输出完整 UI 文案。

| 位置 | `64` 包 | `68` 包 | 影响 |
| --- | --- | --- | --- |
| `channel_settings.end_talk`，`0x13019` | bool、toggle | int、picker，新三项枚举 | 相同地址有不同的值域声明；不能只按旧包 bool 覆盖所有非零值 |
| 信道显示模式枚举 | 3 项 | 4 项，新增频率与名称组合显示 | 字段地址和长度不变 |
| 侧键功能枚举 | 12 项 | 13 项，新增显示时间功能 | 六个侧键短/长按字段共享这张枚举 |
| `call_permit_tone.localKey` | 指向结束提示的翻译 key | 改为许可提示翻译 key | 文案引用修正；存储地址和类型未变 |

本次比较中，表基址、记录步长、数量、字段 offset/length 和读写 formatter 名称均未改变。
“未改变”范围仅限两个已固定 SHA-256 的 schema，不代表固件版本或 APK 的 AOT 实现相同。
APRS 的重叠问题两版均存在。

## 10. 复现与工具验证

从本地官方 APK 生成摘要：

```sh
python tools/inspect_vendor_schema.py /path/to/QUANSHENG-1.1.0-64.apk --output summary.json
```

从已经提取的 JSON 生成 Markdown，或比较两份 APK：

```sh
python tools/inspect_vendor_schema.py /path/to/K8.json --format markdown --output summary.md
python tools/inspect_vendor_schema.py /path/to/old.apk --compare /path/to/new.apk --output diff.json
python -m unittest discover -s tests -p 'test_vendor_schema.py' -v
```

工具不自动下载、不运行 vendor 程序、不解析或写入实机内存，也不把 APK 中的文件展开到磁盘。
它只从 ZIP 内读取精确命名的 JSON；重复成员、重复 JSON key、过大 schema、重复字段 key 和无效尺寸会明确报错。
17 个独立合成测试覆盖 bit/byte 区分、地址覆盖、列表与嵌套列表、跨组重叠、字段越界、显示与存储单位区分、差异比较及 APK 读取边界。

后续优先选择能消除歧义的实验：原厂应用单字段导出差分、formatter 静态恢复、真实固件 getter/setter 仿真。
尤其先解决 APRS `+3/+4` 重叠、扫描成员编码和 `end_talk` 值域，再考虑提供可写配置编辑器。

[official-apk]: https://cms.qsfj.com/api/entries/files/11398?fl-language=en
[shj]: https://tiengu.com/software/
