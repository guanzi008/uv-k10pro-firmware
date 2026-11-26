# UV-K10 Pro 硬件资料核验

更新：2026-11-26。分析对象为 `K10PRO_ENG_v8.02.16`，解码后 SHA-256 为
`061759672cc69a1b00420ce6fae67b0756d3db111f8fa4329cadc940dcd17f17`。

**当前可以建立固件所使用的板级接口，但尚未取得能够独立辨认主要芯片丝印的 UV-K10 Pro 拆机原图。**
这份记录将厂商声明、样本代码证据和实物确认分开。现有结论不构成完整原理图，也不保证适用于每个硬件版本。
来源元数据和下载摘要见 [hardware-sources.json](evidence/hardware-sources.json)。

## 1. 已经能确定到哪一层

| 部件 / 功能 | 现有依据 | 当前结论 | 尚缺的实物证据 |
| --- | --- | --- | --- |
| MCU | 固件的 GPIO、RCC、USB、UID、中断布局与 Puya 资料对应；应用占用超过 64 KiB，RAM 初始化超过 8 KiB | PY32F071 或寄存器兼容实现，128 KiB Flash / 16 KiB SRAM 规格档；详见 [reverse-engineering.md](reverse-engineering.md) | MCU 顶面丝印、封装引脚数、完整订货后缀、板号 |
| MCU 时钟 | 样本初始化软件时钟变量为 48 MHz；Puya 系列资料给出的器件上限为 72 MHz | 软件采用的计时假设与器件最高规格是两件事；没有实测主频 | 启动程序 RCC 状态、晶体标识或示波器测量 |
| 主射频芯片 | 真实函数仿真恢复三线接口：PF5 SCK、PF6 双向 DATA、PF9 低有效 CS；8 位控制地址字段和 16 位数据 | 与 BK48xx 兼容接口比较；尚不能在 BK4819、BK4829 或兼容实现之间定型 | 射频芯片丝印、芯片 ID 实测、板级连通性 |
| 外挂 NOR | SPI2、PA0–PA3；`03/05` 指令、256 字节编程页、4 KiB 擦除扇区的固件操作 | 存在串行 NOR 存储接口；协议地址边界不等于器件容量 | JEDEC ID、SFDP、完整丝印；当前不能确认 4 MiB |
| Si4732 | 官方列为可选；固件 PB8/PB9、地址 `0x11`，GET_REV 预期 PN=`0x20` | 样本带 Si4732 兼容驱动，且与厂商选装说明吻合 | 某一台机器是否实际焊装、模块版本、复位/供电与音频切换线路 |
| LCD | 官方仅给出 1.44 英寸和可调 RGB 颜色；未取得控制器丝印 | 显示控制器、分辨率和总线仍待验证；“RGB”不能单独证明彩色 TFT | 排线型号、LCD 驱动芯片或初始化命令、数据/命令引脚 |
| USB / 写频接口 | 固件包含 PY32F071 原生 USB 外设路径；官网写明机身及电池均有 Type-C 充电 | 有原生 USB 软件路径；不能据此判断所有 Type-C 插座的数据线接法 | USB 描述符实测、D+/D− 连线、是否存在额外桥接芯片；不能指定 CH340/CH9102 |
| 蓝牙 | 官方产品及应用政策明确支持蓝牙写频 | 功能有一手资料确认；蓝牙 SoC/模块型号未知 | 模块丝印、GATT 服务、SPP/BLE 类型和 MCU 连接线路 |
| GNSS / APRS | 官方区分 GPS/APRS 选装版本；应用另支持从手机同步位置 | 存在选装定位功能和手机位置输入两条证据；APRS 功能本身不能证明某颗 GNSS 芯片 | GNSS 模块丝印、天线走线、原始 NMEA、数据接口和供电开关 |
| PCB 版本 | 官方公布功能版本，未提供可核对的 PCB 修订号 | Basic / GPS / Full 是销售配置分类，不能直接映射到 PCB 修订号 | 正反面板号、生产批次、各版未焊焊盘对照 |

表中的固件接口属于本项目样本分析；厂商声明来自 [官方产品页][product]、其公开 CMS 记录和 [官方账号][linkedin]。
已经恢复的 NOR 与 Si4732 地址锚点在 [pinmap.md](pinmap.md)。主射频代码的入口为
`0x08018944`（写寄存器）和 `0x08018248`（读寄存器），作为本版本仿真的定位点，不据此命名实际芯片。

本轮 [真实函数仿真](evidence/rf-bus-emulation.json) 记录了 GPIO 基址 `0x50001400` 的 bit5 / bit6 / bit9。
[OpenPuya CMSIS 头文件][puya-cmsis-raw] 定义 `IOPORT_BASE=0x50000000`、`GPIOF_BASE=IOPORT_BASE+0x1400`，
因此这组信号可命名为 PF5 / PF6 / PF9。调用链包含位循环与数据脚方向切换，外设返回由 MMIO 模型提供；
结果不等于已经在主板上测得电气波形，也不能据此确认主 RF 芯片唯一型号。
实验边界、寄存器对写入与结果解释见 [emulation.md](emulation.md)；具体用例数以该文档及证据 JSON 为准。

### 关于“4MB 外挂存储”

目前可检索到的文章有这一说法，但本轮没有看到原始芯片照片或实测 JEDEC 结果。
不能把多篇转载视为多个独立确认，也不能把寄存器/协议地址常量 `0x001FF000` 当作容量。
报告中使用 `MiB` 表示字节容量、`Mbit` 表示位容量，避免把 32 Mbit 与 4 Mbit 混同。
仓库驱动的 4 MiB JEDEC 白名单只是受支持器件集合，并非 UV-K10 Pro 已装芯片清单。

## 2. 官方型号及配置边界

[泉盛官方账号][linkedin] 的 UV-K10 Pro 发布条目列出三个版本：

| 厂商版本名 | 厂商所列配置差异 | 本项目不能据此推出的内容 |
| --- | --- | --- |
| Basic Version | 标准功能 | 与 GPS 版相同 PCB；具有 GNSS 芯片但仅被软件关闭 |
| GPS Version | Basic 加 GPS / APRS | GNSS 芯片品牌、型号、串口、报文格式 |
| Full Version | Basic 加 GPS / APRS / Si4732 | Si4732 子板布局、天线及音频切换连接 |

官方产品 CMS 条目 `11145` 还直接列出 **999 信道**，这适合与用户界面可见范围交叉核对。
它不能决定存储区的物理槽位总数或特殊记录的数量。
同一条目把 GPS 与 Si4732 列为可选，不能把网页上的全部功能默认赋给每一台机器。

另外，官网对双 Type-C 的表述是充电功能。电池 Type-C、机身 Type-C、K 头写频线和蓝牙属于不同的物理路径，
还需要分别验证传输接口。看见 MCU 原生 USB 驱动时，不应立刻在原理图里补出一个不存在证据的 USB 转串口芯片。

## 3. 对协议逆向有用的官方应用线索

[官方应用政策][privacy]（生效 2026-10-12）把 UV-K10 Pro 的协议机型标为 **K8**，并描述蓝牙读写信道、配置模板、
开机点阵、APRS 信息和手机定位同步。适用 iOS 应用版本为 1.1.0，Bundle ID 为 `com.walkieTalkieMain`。

官方分发 APK 又提供了独立的文件级交叉核对：

| APK 内路径 | 实际读到的内容 | 证据边界 |
| --- | --- | --- |
| `assets/flutter_assets/assets/products/data.json` | 189 字节；产品目录只列 `UV-K10 Pro` | 证明该分发包包含本型号产品目录，不证明所有代码只支持这一个型号 |
| `assets/flutter_assets/assets/K8.json` | 175,298 字节的参数 schema | 可用于逐字段交叉核对；完整厂商 schema 不提交仓库 |
| `assets/flutter_assets/assets/devices/k8.png` | 73,663 字节的设备图资源 | 已确认文件存在；本轮未据此辨认内部硬件 |

结合官方政策的明确对应，`K8` 可以作为本机应用协议分支的检索标识。
以下方向仍需用具体代码和数据证明：

1. 将 `K8` schema 与官方固件读写函数逐字段对应；该名称不证明它与其他商品名为 K8 的电台同板。
2. 模板序列化可用于交叉核对信道记录；模板文件布局不能预先假定等同于整片 NOR 镜像。
3. 定位坐标可能来自手机。若要确认板载 GNSS，应观察断开手机后的接收路径或 GNSS 原始报文。

资产名称和内容已经静态读取；它们本身不能证明蓝牙 UUID 或传输帧协议。相关实现仍应由代码或抓包继续证明。

## 4. 官方下载记录与静态样本

官网前端 `product-0bFSRl5R.js` 的公开文件列表调用是 `GET /api/entries/files`，
带 `filter[products]=11145`、`filter[published]=Yes` 以及语言参数。
2026-11-26 读取中英文列表均得到 6 个公开关联记录，没有单列空白模板或用户手册 PDF。

| CMS 记录 | 官网标题 / 版本 | 取得状态 |
| --- | --- | --- |
| [11434][file-fw] | 英文固件 8.02.16 | 已有样本，身份见 [reverse-engineering.md](reverse-engineering.md) |
| [11411][file-cn-fw] | 中文固件 | 官网指向百度网盘；本轮未下载 |
| [11390][file-cps] | 英文 CPS；文件名 `UV-K10-Pro-0.56.rar` | 已下载原始 RAR，未运行 |
| [11398][file-apk] | Android Bluetooth APP；文件名含 `1.1.0-64` | 已下载原始 APK，未运行；版本号此处只按分发文件名记录 |
| [11237][file-cn-cps] | 中文写频软件 1.2.0-12 | 官网指向百度网盘；本轮未下载 |
| [11234][file-cn-apk] | 中文 Android APP 1.1.0-68 | 官网指向百度网盘；本轮未下载 |

| 静态文件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `UV-K10-Pro-0.56.rar` | 1,178,625 | `4a61d69d342b261175dd5d005fa4d014db5d292f4b5d14161e6ba0a4d77bfaa7` |
| `QUANSHENG-1.1.0-64.apk` | 30,842,842 | `b12adceddfe49c7df04eab3b8b6dbc336d7a6eb32a0df62bc75b7e978559d048` |

原始分发名和下载 URL 记录在证据 JSON；APK 的冗长后缀在本地分析目录中简化，不改变文件内容。
下载成功仅验证包来源和字节身份，不表示安装过、运行过或验证了软件行为。
这些厂商二进制不随仓库分发。

### 经销商镜像，单独记录来源

[SHJ 下载页][shj] 另外提供 `K10-PRO Resource Pack`，本轮取得其 ZIP，大小 **18,509,848 字节**，
SHA-256 为 `c745aac17acece6374ed44b7e645e701516b46a99a2238da07ad7ec8877a5fba`。
压缩包包含文件名标注的 Android `1.1.0-68`、CPS `0.57`、蓝牙 APP 使用说明 DOCX 和功率操作 DOCX。
已静态提取其中的 APK 并比较 K8 参数资产，差异与哈希详见 [settings-map.md](settings-map.md)；未运行应用。
这不是整机手册 PDF；也不能仅因文件版本较新就认定为官方认证或所有机型适用。
其分发方是经销商，不能替代上面两个官方 CMS 下载样本的来源证明。

## 5. 拆机、内照与说明书的检索结果

### 尚未取到可用于读丝印的一手 K10 照片

本轮使用中英文型号、拆机、PCB、internal photos、teardown、芯片型号、原作者呼号以及监管标识等组合检索。
检索日期为 2026-11-26。下表说明当前取得的内容及其限制，**不是断言互联网上没有拆机资料**。

| 线索 | 作者 / 日期 | 实际取得的内容 | 可以怎样使用 |
| --- | --- | --- | --- |
| [什么值得买聚合页][teardown-lead] 所列小红书拆机 | 聚合来源栏标注 CQ 业余无线电、BH4IWK 分享；2026-11-12 | 仅聚合文与原帖标题；没有取得原帖正文、图片或视频时间点 | 后续寻找原图的检索线索；不能据此读出 MCU / Flash / RF 丝印 |
| 同一聚合页所列说明书扫描 | 大大猫；2026-11-09 | 有小红书 note 链接 `6af12a9600000000350141ed`，正文未能读取 | 尚无可引用页码；不能当作已经查看过手册 |
| [BI4OAE 评述][bi4oae] | BI4OAE；2026-11-18 | 作者明确表示尚无实机，引用其他报道 | 不能作为独立拆机或 4 MiB 容量确认 |
| [F4HWN 展会预览][f4hwn] | F4HWN / Armel；2026-10-11 | 作者描述是依据泉盛展位照片的预览 | 外观 / 展示来源，不是拆机证据；本轮未据此判断芯片 |
| [监管索引][fcc-index] | FCCID.io 第三方索引；读取于 2026-11-26 | 可见 UV-K1、TK11、TK11-8、UV-K5 等记录；未取得对应 K10 的认证内照 | 只能说明本次检索未找到；不能证明 K10 无认证 |

上述拆机线索没有可核对的视频时间戳、PCB 图号或清晰器件照片，因此本轮不发布“看图确认”的芯片清单。
外观图、K1 的透明壳图和 K5 改装板图均不能填补这个缺口。

### 容易混淆但已经排除的资料

- [UV-K1 社区首页][k1-community] 的站名虽然加入 UV-K10 Pro，页面的大部分参数与拆机视频仍属于 UV-K1。
  其 8 MB 选装说明、200 信道与 LCD 内容不能迁移为 K10 的硬件事实。
- [mentalDetector 的 PCB 工程][k5-pcb] 明确标识 **UV-K5 R51 V1.4**。高分辨率内层照片有研究价值，但不属于 K10 原理图。
- `XBPUVK1`、`XBPTK11`、`XBPTK118`、`XBPUVK5` 是其他型号的监管标识。
  没有一手材料能将这些型号内照认作 K10 的同版主板。
- 搜索词 `K10` 会命中手机维修与蓝牙音箱认证；必须同时核验厂商、设备类别、型号和拍摄对象。

## 6. 主射频芯片资料：先核接口，再定型号

[Beken 官方 BK4819 产品页][bk4819] 可用于核对三线 MCU 接口、半双工 FM 收发和封装级特征。
它没有证明 K10 装有 BK4819，且共享接口特征不足以区分近亲型号。

另找到厂商署名的 **BK4829 Datasheet，DS-BK4829-E01 V1.0，2023-05-26** 的
[公开镜像][bk4829-ds]。镜像不在 Beken 域名，未取得厂商签名或官方原始下载链；作为待交叉核对的厂商文档副本使用。
第 11 页描述 SCK / SCN / SDATA 三线，8 位控制地址字段接 16 位数据，及上升沿采样、下降沿输出；
第 16–17 页给出引脚说明。这可与固件模拟得到的波形比较，不能由波形相似反推出唯一芯片型号。

**尚未找到可靠的官方完整 BK4829 寄存器表。**
[F4HWN 仓库 discussion #36][bk4829-discussion] 中存在被命名为 BK4829 寄存器表的附件，
但原帖参与者已质疑其可能只是 BK4819 文档改标题。该附件不作为本工程的 BK4829 寄存器权威依据。
对照其他开源驱动时也需按寄存器逐项验证，不能整套照搬 BK4819 配置。

已确定的 MCU 和可选接收器则优先使用 [Puya 数据手册][puya-ds]、
[OpenPuya CMSIS][puya-cmsis] 和 [Skyworks AN332][an332]。
这里的“厂商资料解释了接口”与“板上确有这一器件”是不同的证据层。

## 7. 下一轮最有价值的硬件核验

| 优先级 | 需要取得的证据 | 能解决的问题 |
| --- | --- | --- |
| 1 | 同一台 K10 的背标、板号、主板正反面、MCU/RF/NOR 清晰近照，并注明 Basic/GPS/Full 与固件版本 | 唯一器件型号、封装后缀、不同销售版本是否同板 |
| 1 | NOR `9F` 响应与厂商数据手册，必要时只读 SFDP | 物理容量、擦除几何、状态寄存器定义 |
| 1 | 原厂程序主 RF 三线总线的寄存器读回、初始化和仅接收调谐序列 | 将静态/仿真推测与真实器件行为相互核验 |
| 2 | LCD 初始化命令、排线型号、帧缓冲写入长度 | 控制器系列、分辨率、总线、RGB 背光与像素颜色的区别 |
| 2 | 蓝牙模块丝印、服务发现结果和官方 APK/CPS 中 K8 分支 | 传输链路及参数模型；确认 USB 与蓝牙是否共用上层命令 |
| 2 | GNSS 原始串口报文、模块标识、断开手机后的运行记录 | 内置 GNSS 型号、波特率、位置来源与电源控制 |
| 3 | Type-C 数据线走线 / USB 描述符及电池接口近照 | 原生 USB 到哪个端口、是否另有桥接芯片、充电与写频路径 |

这些项目用于缩小未决接口，不要求先猜完所有 GPIO 才继续信道格式及读写协议工作。
仿真应优先复用真实固件函数、记录输入与外设访问，然后用一手资料解释；缺少外设电气行为时，结果只能证明函数逻辑，不能证明射频或音频实际表现。

[product]: https://qsfj.com/products/11145
[linkedin]: https://jp.linkedin.com/company/quanshengradios
[privacy]: https://qsfj.com/privacy-policy.html
[file-fw]: https://cms.qsfj.com/api/entries/files/11434?fl-language=en
[file-cn-fw]: https://cms.qsfj.com/api/entries/files/11411?fl-language=zh
[file-cps]: https://cms.qsfj.com/api/entries/files/11390?fl-language=en
[file-apk]: https://cms.qsfj.com/api/entries/files/11398?fl-language=en
[file-cn-cps]: https://cms.qsfj.com/api/entries/files/11237?fl-language=zh
[file-cn-apk]: https://cms.qsfj.com/api/entries/files/11234?fl-language=zh
[teardown-lead]: https://post.smzdm.com/p/awwdov6p/
[bi4oae]: https://www.bi4oae.top/blog/quansheng-uv-k10-pro-review
[f4hwn]: https://www.youtube.com/watch?v=CDfujLy33So
[fcc-index]: https://fccid.io/company/Quansheng-Electronics-Co-Ltd
[k1-community]: https://uv-k1.com/
[k5-pcb]: https://github.com/mentalDetector/Quansheng_UV-K5_PCB_R51-V1.4
[bk4819]: https://www.bekencorp.com/en/goods/detail/cid/50.html
[bk4829-ds]: https://manuals.plus/m/b03fb6dba016158ab9f4b5b3d1aeb1d9ea178efc44cc11c2c08398621553cdc9.pdf
[bk4829-discussion]: https://github.com/armel/uv-k1-k5v3-firmware-custom/discussions/36
[puya-ds]: https://www.puyasemi.com/download_path/%E6%95%B0%E6%8D%AE%E6%89%8B%E5%86%8C/MCU%20%E5%BE%AE%E5%A4%84%E7%90%86%E5%99%A8/PY32F071%E7%B3%BB%E5%88%97%E6%95%B0%E6%8D%AE%E6%89%8B%E5%86%8C_V1.4.pdf
[puya-cmsis]: https://github.com/OpenPuya/PY32F071_Firmware/blob/master/Drivers/CMSIS/Device/PY32F071/Include/py32f071xB.h
[puya-cmsis-raw]: https://raw.githubusercontent.com/OpenPuya/PY32F071_Firmware/master/Drivers/CMSIS/Device/PY32F071/Include/py32f071xB.h
[an332]: https://www.skyworksinc.com/-/media/Skyworks/SL/documents/public/application-notes/AN332.pdf
[shj]: https://tiengu.com/software/
