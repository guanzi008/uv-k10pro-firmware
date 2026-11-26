# UV-K10 Pro 型号隔离与应用路径审计

本次审计把 **UV-K10 Pro 商品名称、Android 的协议标识 K8、Windows 程序的 K7 命名空间、连接回复中的参数版本字节** 分开核对。它们属于不同层次，不能互相替代。

结论是：**本项目使用 K8 的 999 × 64 字节信道布局；K82 和 UV5 资产不能用于补齐 K10 的参数或硬件。PC CPS 0.56 的 K7 命名空间确实属于这份 UV-K10 Pro 程序。先前发现的调制枚举差异仍然存在，不能用“拿错机型”解释掉。**

可机器检查的摘要见 [model-identity.json](evidence/model-identity.json)。所有 APK、EXE、厂商 JSON 全文和原固件字节均由分析者另行取得，本仓库只提供工具和独立分析结果。

## 1. 证据范围

| 样本或信息 | 来源 | 本次能够证明的范围 |
|---|---|---|
| QUANSHENG `1.1.0-64` | [厂商 CMS 文件 11398][apk]，与[产品 11145][product]关联的直接下载 | 固定 APK 中的产品目录、参数资产、AOT 字符串 |
| QUANSHENG `1.1.0-68` | 前一轮取得的经销商资源包 | 这份样本的资产与 64 的差异；不能只按版本号认定与厂商百度网盘文件逐字节相同 |
| Android 68 的官方条目 | [厂商 CMS 文件 11234][apk68] | 厂商也发布了该版本标签；本轮没有从此网盘独立取得第二份二进制做哈希同一性验证 |
| UV-K10 Pro CPS 0.56 | [厂商 CMS 文件 11390][cps] → RAR → Setup Factory 7 → 真正的 .NET 主程序 | 主程序资源、方法引用、结构与 IL 常量；未运行 EXE 或 CLR |
| `K10PRO_ENG_v8.02.16_publish.bin` 的解码应用 | 前述官方固件分析的固定 SHA-256 样本 | 原始 ARM 函数的静态分析及明确隔离的指令重放 |
| 25Q32、MCU 与此前推测一致 | 本轮用户确认 | 来源标记为 `user-report`，不改写成新的拆机实测或电子识别结果 |

关键哈希如下；其余资产哈希见 JSON。

| 文件 | SHA-256 |
|---|---|
| APK 64 | `b12adceddfe49c7df04eab3b8b6dbc336d7a6eb32a0df62bc75b7e978559d048` |
| APK 68（经销商包内样本） | `0395fa0332d025da73a39aad49b01eaedfe59e8296f17df56fa937ff9b83b326` |
| CPS 真正主程序 | `79c9b3429187f7fca13cc8971d0e5fd46184f078127f149f333a267b99815132` |
| 8.02.16 解码应用 | `061759672cc69a1b00420ce6fae67b0756d3db111f8fa4329cadc940dcd17f17` |

## 2. Android：同一 APK 包含多个协议，K10 对应 K8

厂商[应用隐私政策第 4 节][privacy]明确把 UV-K10 Pro 与协议机型 K8 对应起来。两个 APK 的 `assets/products/data.json` 都只有 `UV-K10 Pro` 一个产品目录项；这份目录的 SHA-256 也相同。但是，两个包同时存在 `K8.json`、`K82.json`、`UV5.json` 以及多个协议后端。因此，产品目录不能用来推断 APK 中每个参数字段都属于 K10。

下表为分别读取各资产自身的 `deviceModel` 后得到的几何信息，没有把不同 schema 合并。64 和 68 的这些主要几何量相同。

| 项目 | K8：本项目采用的 K10 路径 | K82：排除 | UV5：排除 |
|---|---|---|---|
| `deviceModel` | `K8` | `K82` | `UV5` |
| 普通信道数量 | 999 | 200 | 10000 |
| 普通信道基址 | `0x000000` | `0x000000` | `0x410000` |
| 普通信道记录跨度 | 64 字节 | 16 字节 | 由独立 schema 定义；不用于 K10 |
| VFO | `0x10000`，14 × 64 字节 | `0x0C80`，14 × 16 字节 | 不用于 K10 |
| 普通信道属性表 | `0x11000` | `0x0D60` | `0x516000` |
| 普通信道名称 | 64 字节记录内 `+0x18` | 独立表 `0x0F50`，200 × 16 字节 | 不用于 K10 |
| 扫描列表 | `0x16000`，32 × 128 字节 | `0x00A9`，2 × 3 字节 | `0x20C000`，250 项 |
| DTMF 联系人 | `0x1A000`，16 × 24 字节 | `0x1C00`，16 × 16 字节 | 不用于 K10 |

K8 的 999 × 64、VFO `0x10000`、属性 `0x11000` 与此前直接执行 K10 原固件函数得到的布局吻合，CPS 的结构与读写循环也独立吻合。它不只是一个像 K10 的文件名。交叉证据见 [channel-storage.md](channel-storage.md) 与 [cps-protocol-crosscheck.json](evidence/cps-protocol-crosscheck.json)。

### K82 是什么型号？

**当前证据不足以给 K82 指定一个商品型号。** 它有独立的 200 信道、16 字节记录布局和 `K82RadioProtocol` 后端。即使部分 helper 仍叫 `k8FlagRead`，也不能据此把 K82 当作 K8 的同义词。名字相似、相同 helper、旧型号的相似容量，都不能替代商品型号映射证据。

同样，UV5 的数字信道、加密或其他调制字段不能拿来宣称 K10 支持这些功能。这里的 UV5 是资产内部标识，本文没有把它直接等同于某一商品名。

### 已找到路由线索，尚未恢复完整 Dart 路由

两个 `arm64-v8a/libapp.so` 都保留以下 AOT 字符串：

- `protocolIdForModelName`、`modelNameForProtocolId`、`normalizeModelId`；
- `selectProtocolForDeviceModel`、`preferredDeviceModel`；
- `K8RadioProtocol` / `K82RadioProtocol`、`K8Session` / `K82Session`；
- `assets/K8.json`、`assets/K82.json`、`assets/UV5.json`、`UV-K10 Pro`。

这说明应用有按机型选协议的结构线索，也证实多个后端被一起打包。**字符串位置不是 Dart 方法地址，本轮没有恢复 `protocolIdForModelName` 的完整 AOT 控制流，也没有运行 Android 应用观测蓝牙设备名如何选分支。**

因此，本项目的准确表述为：“厂商明确指定 K10 的协议标识为 K8；固定 APK 的 K8 布局与 K10 固件/CPS 相互印证。”不能扩大为：“已经实测任何名称相近的设备都会自动正确识别为 K10。”

## 3. Windows：K7 是代码命名空间，程序产品是 UV-K10 Pro

真正的 CPS 主程序为 910336 字节。对其静态提取的资源和 IL 进行核对，得到以下链条。

| 位置 | 可复核结果 | 含义 |
|---|---|---|
| `K7.Resource.resources` 的 `model` | `UV-K10 Pro/UV-K10 Pro` | 两种界面语言的产品名称都是 K10 Pro |
| `main::.ctor`，MethodDef `0x06000399`，RVA `0x63C60` | `Version 0.56` | 与下载包版本相符 |
| `main::main_Load`，`0x060003A8`，RVA `0x64A94` | 读取 `model` 资源并设置窗口标题 | 产品资源有实际 UI 使用路径 |
| `main::.cctor`，`0x06000453`，RVA `0x6E4E0` | `ModelVersion = 0`；分配 999 个信道对象 | 固定参数版本及信道规模 |
| `main::Login`，`0x060003CF`，RVA `0x67AAC` | 调用 `protocol_struct.Link(3)`，比较 `protocol_struct.Model` 与 `ModelVersion` | 实际连接检查路径 |
| `protocol_struct::Link`，`0x060002BC`，RVA `0x49CEC` | 把回复结构的 `u8ParamsVersion` 赋给 `Model` | `Model` 这个变量名实际接的是参数版本字节 |

这些结果足以排除“因为 namespace 叫 K7，所以分析的是另一款 K7 软件”的判断。程序中也有共享式参数辅助函数及另一套 `multi_protocol_struct`；对某个结论仍然必须追踪具体调用者。文件归属正确，并不等于每个未引用的 helper 都是该型号可达功能。

### 参数版本 0 不能当作唯一商品型号 ID

连接回复的字段位于**包括 4 字节内部消息头在内的 body 偏移 `+22`**，即 `MSG_MSCAL_ConnRsp.u8ParamsVersion`。静态链条是：

`ConnRsp.u8ParamsVersion → protocol_struct.Model → main.Login 的版本比较`。

本次还实际重放了固定固件的回复构造函数 `0x080035B8`：

| 项目 | 结果 |
|---|---|
| 真实执行 | Cortex-M0 指令模型中的原始函数；先运行原 `.data` 解压初始化 |
| 替换边界 | 仅 `0x0800361C` 的发送函数被替换为内存捕获 |
| 回复 body | 56 字节；命令 `701 / 0x02BD`；内部声明长度 52 |
| 版本字符串 | `8.02.16` |
| `body[22]` | `0`，与 CPS 预期一致 |
| 回复构造指令数 | 300；一次传输 hook；未映射 MMIO、无物理串口或蓝牙 |

这验证了这份 CPS 与这份固件的**参数版本相容性**。没有证据说明所有设备的参数版本 0 都只属于 UV-K10 Pro，因此主机工具不能据此单独判断型号或自动允许写入。

## 4. FM / AM / CW 差异确实处在 K10 的读写路径

`param_string.chn_demode_Items`（MethodDef `0x060002E1`，RVA `0x4AD1C`）返回 `FM, AM, CW`。它不仅是程序集里一份无人引用的旧表：

| 路径 | MethodDef / RVA | 与枚举的关系 |
|---|---|---|
| 原始信道 → UI 信道 | `0x06000025 / 0x6D60` | 用此表和 `GetSafeItem` 把原始 `demode` 转成 UI 的 `am` 字段 |
| UI 信道 → 原始信道 | `0x06000028 / 0x70E0` | 用此表和 `GetIndex` 把 UI `am` 反写到 `demode` |
| 信道编辑窗加载 | `0x06000324 / 0x4D140` | 此表填入 `comboBox15` |
| 主表单元格编辑 | `0x0600042F / 0x6C124` | 直接把此表传给下拉框加载函数 |

APK 64 和 68 的 **K8** 资产 `demode_option` 也都是三项 `FM, AM, CW`。固定 8.02.16 固件已重放的菜单格式化路径则有四项 `FM, AM, SSB, CW`。它们属于同一目标产品的不同软件层/版本证据，而不是本轮混入 K82 后制造的差异。

| 原始枚举值 | 8.02.16 固件的显示 | CPS 0.56 转换表的显示 |
|---|---|---|
| 0 | FM | FM |
| 1 | AM | AM |
| 2 | SSB | CW |
| 3 | CW | `GetSafeItem` 越界后退回首项 FM |

`GetSafeItem` 的位置为 `0x06000023 / 0x6C6C`；`GetIndex` 为 `0x06000026 / 0x7074`。后者遇到不在列表中的文本也返回 0。因此，**按这两个转换器串接，原始值 3 有被显示为 FM、再编码为 0 的静态可推导风险**。本轮没有运行 CPS 完成端到端导入导出，不能声称已经观察到实机被它修改。

本项目继续按固定固件保存原始枚举值，并显示明确的版本来源，不用三项应用枚举重编号。`SSB` 出现在格式化器中也不等于该 RF 模式已在实机验证。广播芯片的 AM/LSB/USB 枚举与主信道的 `demode` 也应分别解释。

## 5. 软件选项与硬件确认分别记录

`GetRadioChipTypes`（`0x060002CD / 0x4A6E4`）列出 `BK1080` 和 `Si4732`；`main_Load` 实际选择索引 1，写入 `RadioModul`。这比单独搜到芯片字符串更强，但仍是软件默认选择；物理器件的证据继续来自固件总线行为和板卡材料，不把软件下拉框当成焊接清单。

用户本轮确认的信息单独记录如下：

| 项目 | 当前采用值 | 证据身份 |
|---|---|---|
| 外置 NOR 容量 | 25Q32 密度等级，32 Mbit = 4 MiB = 4194304 字节 | `user-report`；尚无本轮新的 JEDEC 实读或完整丝印照片 |
| NOR 地址范围 | 容量换算为 `0x000000…0x3FFFFF`；上界不含端点为 `0x400000` | 从用户容量信息计算，不等于所有地址都由写频协议开放 |
| MCU | 用户认可此前 PY32F071 系列推定 | `user-report` 加此前固件寄存器/向量/启动代码证据；本轮未新增封装、具体后缀或晶粒识别实测 |

此前某些实验使用的 2 MiB `NorModel` 是明确给定的人工后备存储大小。写频处理器里 `0x1FF000` 的主机请求起址限制也只是命令接受条件；这两个数字都不能推翻 4 MiB 的器件容量。要处理上半区的真实用途，仍须按原固件调用点逐一定位，不能只把写频边界整体扩大。

## 6. 对现有结论的处置

| 现有结论或候选信息 | 本次处置 |
|---|---|
| K8 的 999 × 64 信道、14 × 64 VFO、`0x11000` 属性表 | 保留；固件/CPS/K8 三方独立一致 |
| CPS `.NET namespace = K7` | 标明为内部代码命名；其历史来源未证实，不作为硬件型号 |
| CPS 参数版本 0 | 保留相容性证据；禁止升级为唯一型号识别 |
| FM/AM/CW 与固件四项枚举冲突 | 保留并补上实际读写转换调用链；不是“另一个型号的死代码”结论 |
| Android 按设备名自动选 K8 的完整实现 | 未完成；只有厂商映射、布局交叉核对和 AOT 路由符号 |
| K82 商品名、其 200 × 16 布局对应 K10 | 前者未知，后者排除 |
| UV5 的字段、数字/加密功能推导 K10 硬件 | 排除 |
| 使用 PC/APK 的候选按键和屏幕设置推导物理接线 | 只能提示待查功能；GPIO 必须来自该固定固件或对应板卡证据 |
| 25Q32 / PY32F071 用户确认 | 纳入来源明确的设备配置，保留实测边界 |

## 7. 复现

使用 `requirements-re.txt` 中的静态分析/指令模型依赖，并自行提供上述固定样本：

```sh
python tools/audit_model_identity.py \
  --apk /path/to/QUANSHENG-1.1.0-64.apk \
  --apk /path/to/QUANSHENG-1.1.0-68.apk \
  --cps '/path/to/extracted/UV-K10 Pro.exe' \
  --raw /path/to/8.02.16-decoded.raw.bin \
  --output /path/to/model-identity-generated.json

python -m unittest discover -s tests -p 'test_model_identity.py' -v
```

工具在解析前核对 APK/CPS 哈希；只接收本次记录的样本。它要求资产文件名与 `deviceModel` 精确对应，再独立比较信道/VFO 几何。`K8` 不能通过前缀匹配接受 `K82`，只改文件名或商品标签也不能越过检查。通过几何检查仍会明确输出 `live_device_identity_verified: false`，因为离线资产相容不等于已连接设备被鉴别。

发布的 JSON 在可重生成摘要之外还附带来源、人工控制流解释和用户确认字段；这些注释不伪装为工具自动实测。

[product]: https://qsfj.com/products/11145
[privacy]: https://qsfj.com/privacy-policy.html
[apk]: https://cms.qsfj.com/api/entries/files/11398?fl-language=en
[apk68]: https://cms.qsfj.com/api/entries/files/11234?fl-language=zh
[cps]: https://cms.qsfj.com/api/entries/files/11390?fl-language=en
