# Board interfaces recovered from the application

This is a **firmware-derived board profile** with an owner-confirmed PY32F071
family MCU and 25Q32-density NOR (32 Mbit / 8 = 4 MiB). It is not a checked
schematic or a report of successful operation on a physical radio. See
[model identity and evidence provenance](model-identity.md). It targets the interface used by
`K10PRO_ENG_v8.02.16`. Board revisions and optional components still need to be
identified on hardware.

The independently implemented drivers use the SPI NOR, Si4732, LCD/backlight
and optional keypad pins below.
The RF and programming-interface sections record additional research evidence;
those interfaces are not configured by the diagnostic firmware. Unknown reset,
power, speaker, antenna-switch and transmit signals have no guessed definitions.

## Evidence identity

The decoded application was mapped at `0x08002000`. Its SHA-256 is
`061759672cc69a1b00420ce6fae67b0756d3db111f8fa4329cadc940dcd17f17`.
The addresses in this document refer to that image. They are reproducibility
anchors, not symbols from an original source tree. No vendor binary, patch or
proprietary disassembly is included in this repository.

## SPI NOR

| Signal | MCU pin | Configuration | Firmware evidence |
| --- | --- | --- | --- |
| SCK | PA0 | Alternate function 8 | GPIO initialization in `0x0801A298` sets pin mask `1`, AF8 |
| MOSI | PA1 | Alternate function 9 | Same routine sets pin mask `6`, AF9 for PA1/PA2 |
| MISO | PA2 | Alternate function 9 | Same pair; signal names match Puya table 3-3 |
| CS# | PA3 | GPIO output, active low | `0x080195E4` writes bit3 to GPIOA BRR; completion writes BSRR |
| Controller | SPI2 | Base `0x40003800` | Byte transfer at `0x0801A504` uses SR and DR at offsets 8 and 12 |

The GPIO initializer called at `0x0801B040` establishes the parameter layout, so
the AF numbers are traced through the call rather than inferred from a familiar
STM32 pinout. [Puya's PY32F071 datasheet, table 3-3][puya-ds] confirms PA0/AF8 as
SPI2_SCK and PA1/PA2 AF9 as SPI2_MOSI/SPI2_MISO. The PA1/PA2 signal labels in
the earlier project revision were reversed. They are corrected against the
manufacturer's Chinese v1.4 table 3-3 (page 53), pin table 3-2 (pages 34–35),
and English v0.7 table 3-3. Both pins remain AF9, so this correction does not
change the driver's GPIO register configuration. Do not use the old labels
for wiring.

The initializer enables SPI2 using RCC `+0x3C`, bit14, and GPIOA using RCC
`+0x34`, bit0. Its SPI parameters produce CR1 `0x0307` before enabling SPE:
mode3, 8-bit, MSB first, master, software NSS and peripheral clock divided by 2.
The SPI initializer at `0x0801B30C` confirms the combination of those fields.
The independent driver uses the same mode with **PCLK / 256** for initial
bring-up, and polling rather than the vendor's DMA path. These offsets and the
SPI2-only reset bit at RCC `+0x2C`, bit14, also match the
[Puya CMSIS device definitions][puya-header].

Only PA0–PA3 and SPI2 are configured. CS is driven high before output mode is
enabled. SPI DR uses byte accesses. Every wait has both a time limit and an
iteration limit; transactions release CS on failure. A peripheral transfer
failure invalidates the bus state until reinitialization.

### Read protocol and size handling

| Fact | Evidence or implementation decision |
| --- | --- |
| Array read `0x03` with a 24-bit, most-significant-byte-first address | `0x080195E4`, address writer `0x08019652` |
| SR1 read `0x05`, busy in bit0 | `0x08019622`, `0x08019674` |
| Vendor page and sector geometry | `0x080197B2` splits at 256 bytes; `0x08019694` aligns/cache-updates 4096 bytes |
| New read-only driver also sends `0x9F` | Standard JEDEC identification from the manufacturer documents below; not claimed as an observed vendor-driver operation |
| Vendor host protocol has a start-address bound `0x001FF000` | Comparisons at `0x0800323A` and `0x08003394`; this does **not** establish physical capacity |
| This driver bounds the entire read interval | The size comes from an exact supported JEDEC ID; `len <= capacity - address` avoids addition overflow |

The initial exact-ID table is deliberately small:

| JEDEC bytes | Documented part | Capacity |
| --- | --- | --- |
| `85 60 16` | [Puya P25Q32H, section 10.44][puya-nor] | 4 MiB |
| `C8 40 16` | [GigaDevice GD25Q32C, ID definitions][gd-nor] | 4 MiB |
| `EF 40 16` | [Winbond W25Q32JV IQ/JQ, section 7.1][wb-nor] | 4 MiB |
| `EF 70 16` | [Winbond W25Q32JV IM/JM, section 7.1][wb-nor] | 4 MiB |

The owner's 25Q32 confirmation establishes density, not manufacturer or suffix.
This table is **not identification of the exact chip fitted to a UV-K10 Pro**. An
unknown but readable ID is returned unchanged, capacity remains zero, and array
reads return `SPI_NOR_UNSUPPORTED_ID`. Add other IDs only with their supporting
datasheet, or implement and validate SFDP parsing. The read-only interface has no
program, erase, write-enable, status-write, unlock or patch-upload operation. It
does not wake a chip left in an undocumented deep-sleep or QPI state.

## LCD and RGB backlight

Actual execution of the stock GPIO, SPI1, LCD initialization and refresh
functions establishes a 128×64 monochrome page buffer: eight pages of 128
bytes, 1024 bytes total, with LCD column offset +4. The serial configuration is
mode 3, 8-bit MSB-first, PCLK/16 (`SPI1_CR1=0x035F`). The colors are three
backlight PWM channels. Detailed instruction and settings-loader evidence is
in [display.md](display.md) and [display-analysis.json](evidence/display-analysis.json).

| Signal | MCU pin | Configuration / destination |
| --- | --- | --- |
| LCD SCK | PA5 | SPI1 AF0 |
| LCD data out | PA7 | SPI1 MOSI AF0 |
| LCD CS# | PB2 | GPIO output, active low |
| LCD D/C | PF2 | GPIO output, low command / high data |
| Red backlight | PB6 | AF11, TIM1_CH3 / CCR3 |
| Green backlight | PA15 | AF2, TIM2_CH1 / CCR1 |
| Blue backlight | PB5 | AF1, TIM3_CH2 / CCR2 |

All three timers use PSC=15, ARR=2500, compare=`setting * 25`, with settings
0..100. The independent driver initializes them at zero; the diagnostic
application requests (25,25,25) after successful initialization. It owns
TIM1/2/3 and SPI1, uses bounded polling, releases CS and turns PWM off on bus
failure. A successful write-only SPI transaction does not detect panel presence.

The command family is compatible with ST7565/ST7567-style monochrome
controllers; the exact controller is not identified. Stock software sends
`E2` reset. No independent hardware RESET/power pin appears in the executed
LCD path. PF2 can also serve NRST depending on option bytes; this project does
not alter option bytes or assume another reset wiring. These are MCU port
names, **not LCD connector numbers or MCU package pad numbers**.

## Keypad and PTT

Stock scanning function `0x08018C54` establishes these logical contacts.
Columns are active-low inputs with pull-ups. Row outputs are push-pull,
pull-up, speed 3; an idle scan leaves every row high.

| Selected row | PB15 | PB14 | PB13 | PB12 | PA8 |
| --- | ---: | ---: | ---: | ---: | ---: |
| PF8 | 17 | 1 | 4 | 7 | 10 |
| PB4 | 12 | 2 | 5 | 8 | 11 |
| PB3 | 13 | 3 | 6 | 9 | not sampled |
| PA14 | 18 | 14 | 0 | 15 | 16 |

With all rows high, the scanner first checks PB15→24 (SK1), PB14→23 (SK2),
PB13→25 (SK3), in that priority order. PC13 is a separate active-low input,
producing PTT key code 21. See [keypad.md](keypad.md) for key semantics, stock
debounce/hold behavior, simulation boundaries and the unconfirmed labels.

The new driver configures only these pins, not the unrelated outputs in the
stock whole-board GPIO initializer. Its diagnostic filter is independently
specified: 20 ms of unchanged samples, with press/release event flags and
independent PTT state. It does not implement the stock menu or long-press
actions, and PTT is not connected to an RF operation.

PA14 is also SWCLK. `K10_ENABLE_KEYPAD=0` is therefore the default: the keypad
API returns `DISABLED` without MMIO and leaves inherited PA14 state untouched.
This does not establish that SWD is already usable after every bootloader
handoff. Opting in with `K10_ENABLE_KEYPAD=1` takes PA14 for the matrix. The
contact model does not prove the board's diode isolation, safe arbitrary
multiple-key shorts, ghosting behavior or external switch/connector numbering.

## Si4732 receiver

The owner reported Si4732 in this research unit on 2026-11-26. This records
component population as a user report, not a new measurement of the bus below
or a claim that every K10 Pro variant includes it. The full suffix, package,
reset/power routing and reference-clock wiring remain unresolved. On 2026-11-27
the owner explicitly identified the FM radio as BK1080. The earlier Si4732
report and the stock Si4732 code path remain recorded separately; coexistence,
variant selection and audio routing are not established by those reports alone.

| Signal or field | Mapping | Evidence |
| --- | --- | --- |
| SCL | PB8, software GPIO | Bit `0x100` pulses at `0x080191F8`, `0x0801927C` |
| SDA | PB9, software GPIO | Bit `0x200` is written and sampled; `0x0801B12A` changes its direction |
| I2C address | 7-bit `0x11` | Write byte `0x22` at `0x0801A1C2`, read byte `0x23` at `0x08019D76` |
| START / STOP | PB9 transition while PB8 high | Routines `0x08019314`, `0x08019340` |
| POWER_UP | Bytes `01 10 05` | Probe at `0x08019C34`, argument data at `0x08019C84` |
| GET_REV | Command `0x10` | `0x08019BB0`, command data at `0x0801E030` |
| Expected part number | `0x20` = decimal32 | Comparison at `0x08019C60`, interpreted using AN332 |
| Reset, power enable and analog routing | Unresolved | No such MCU GPIO is configured by this driver |

The new bus implementation uses open-drain outputs, released-high lines and
input sampling. It waits for SCL to actually rise, checks each ACK and ends a
response with NACK. Nominal half periods are 10 microseconds. A stuck bus is
reported; blind bus-clearing clocks and guessed reset pulses are not attempted.

The [Skyworks AN332 programming guide][an332] defines the observed POWER_UP
arguments as FM receive, crystal oscillator enabled and analog output. The
driver waits 500 ms after command completion, reads nine bytes for GET_REV
(status plus eight defined Si473x response bytes), and requires PN `0x20`.
Revision fields remain raw numeric/ASCII bytes. A different PN is returned in
the output structure with `SI4732_UNEXPECTED_PART`.

The optional `si4732_fm_tune()` accepts 6400–10800 in 10 kHz units, sends
FM_TUNE_FREQ, polls GET_INT_STATUS for STC, then acknowledges completion through
FM_TUNE_STATUS and checks the frequency returned. These are documented receive
commands. Successful completion says the receiver command interface accepted
the tune; it does not demonstrate speaker output or the board's RF signal path.
Initialization does not automatically tune to a station.

The stock image contains an additional path at `0x08019AF4` that transfers
`0x3DD8` bytes from external NOR offset `0xA0000` in eight-byte chunks. That
external patch is not present in the MCU download and is not used or distributed
here. AM/SSB and this patch path remain outside the initial driver.

## BK1080 FM receiver: owner-reported component

On 2026-11-27 the owner identified the FM receiver as **BK1080**. Beken's
[official product page][bk1080] documents an I²C-controlled FM family with
32.768 kHz crystal support. Its body also names BK1080H; it does not establish
the exact suffix or package fitted to this radio.

The board's BK1080 SCL/SDA pins, I²C address, power/reset, reference clock and
audio connections remain unresolved. The PB8/PB9 and `0x11` Si4732 mapping above
must not be copied into a BK1080 driver without matching evidence. This project
has no independent BK1080 firmware driver yet. The shared FM preset storage
layout belongs to the host configuration model, not a proof of shared chip pins.

## Main RF register bus: BK4829 target, firmware-derived pins

The stock functions `0x08018944(reg, value16)` and `0x08018248(reg)` use the
port at `0x50001400`. The [Puya CMSIS definitions][puya-header] identify this as
GPIOF. Actual instruction replay with an explicit GPIO register model confirms:

| Signal | Firmware-derived MCU pin | Evidence |
| --- | --- | --- |
| Serial clock | PF5 | BSRR/BRR bit5 transitions in `0x08018588` and `0x08018530` |
| Bidirectional data | PF6 | Output bit6, IDR bit6, MODER input/output switching |
| Chip select, active low | PF9 | Bit9 wraps each register transaction |
| Write format | Register byte, then 16-bit value, MSB first | `reg=0x38,value=0x1234` produces `38 12 34` |
| Read format | `reg | 0x80`, then 16 input bits | Artificial `A5 5A` returns `0xA55A` |
| 32-bit register pair | Low word to `0x38`, high word to `0x39` | Actual helper `0x08018480` |

The owner supplied the **BK4829** part identity on 2026-11-27. This is a separate
source from the replay: the observed BK48xx-style three-wire interface cannot
uniquely distinguish BK4819 from BK4829, establish a complete register map or
measure electrical timing. See [the replay and its limitations](emulation.md) and
[manufacturer/teardown sources](hardware-research.md). No main-RF driver has been
enabled in the experimental firmware on the strength of this model alone.

## Programming interfaces: recovered stock configuration

| Interface | Observed stock mapping | Limit |
| --- | --- | --- |
| USART1 | `0x40013800`, PA9/PA10 AF1; 38400/115200 configuration paths | No measured baud rate or connector pinout |
| Native USB | CDC ACM descriptors, VID `36B7`, PID `0001`, data OUT `02`, data IN `81` | Descriptor parse, not physical enumeration |
| Auxiliary USART3 | `0x40004800`, PB11 AF4; PB10 AF4 configured by a separate function | Bluetooth/GPS module connection not established |

The common framing and each transport's checksum/XOR behavior are documented in
[programming-protocol.md](programming-protocol.md). A USB VID by itself does not
identify the MCU. The descriptor text `K8 CPS` is a product/software clue; no
assumption about an interchangeable K8 radio follows from that string.

## ADC packet sampling input: PB1 / ADC_IN9

The 2026-11-28 static review connects the stock ADC/DMA sampling mode to
**ADC channel 9**, and the Puya PY32F071 v1.4 datasheet p39 maps that channel
to **PB1**. The same initialization configures PB0 and PB1 as analog inputs.
The non-DMA conversion path switches to channel 8, mapped to PB0, and feeds
the battery/voltage processing path. In DMA mode the ordinary read function
returns its cached pre-DMA value.

| Item | Fixed-sample anchor | Boundary |
| --- | --- | --- |
| PB0/PB1 analog mode | `0x080173BC..0x080173CC`, GPIOB mask 1/2, mode 3 | GPIO configuration, not measured traces |
| ADC_IN9 for packet sampling | `0x080174AC..0x080174B4`, argument `0x05B00009` | PB1 per [Puya pin table][puya-ds] |
| ADC_IN8 after DMA mode | `0x0801755C..0x08017564`, argument `0x05800008` | PB0 per the same pin table |
| Channel argument decoding | `0x0801AD70..0x0801AD78`, low 5 bits into conversion sequence | Confirms these are channel numbers |

No BK4829 audio-output pad, Si4732/BK1080 audio switch, or battery-divider
resistor has been mapped to these MCU pins. Package pad numbers are not
assigned without the exact MCU suffix. See [the audio analysis](bluetooth-audio-feasibility.md)
and [byte anchors](evidence/ble-audio-static-anchors.json).

## PHY6235SC Bluetooth SoC and unresolved CS6150XC

The owner identified **PHY6235SC**, **BLE 5.4** and **RISC-V** on 2026-11-27.
The [PHY6235 family page][phy6235] supports BLE 5.4 and a 32-bit RISC-V CPU;
this does not change the PY32F071 main-MCU architecture. See
[Bluetooth hardware and programming links](bluetooth-hardware.md) for the
family specifications and application-level evidence.

No PHY6235SC package pad, host UART/SPI/USB interface, reset, wake-up or power
signal has been mapped to the K10 board. In particular, the USART3/PB10/PB11
stock configuration above is not sufficient to assign that UART to Bluetooth.
The 2026-11-28 manufacturer-datasheet review now supplies an SC/SOP16 device
pin reference in [bluetooth-hardware.md](bluetooth-hardware.md); these device
pins are not measured board connections. The same investigation confirms the
stock USART3 initialization argument of 115200 and an ADC/DMA-to-APRS packet
path; see [bluetooth-audio-feasibility.md](bluetooth-audio-feasibility.md).
The separate owner-reported marking **CS6150XC** remains functionally unresolved;
no pin or peripheral definition is assigned to it.

## Owner-reported GNSS, crystal and antenna components

The owner supplied **ATGM336H-5N31**, a **10 × 10 mm GPS antenna**, a
**32.768 kHz SMD3215-2P crystal**, and an **SMA female antenna connector** on
2026-11-26. The manufacturer lists the GNSS variant as **ATGM336H-5N-31**.
Its module-level UART/PPS/power pin reference and exact source boundaries are
recorded in [gnss-hardware.md](gnss-hardware.md).

No K10 MCU pin has yet been assigned to GNSS TXD0/RXD0, PPS, ON/OFF or nRESET.
In particular, the USART3/PB10/PB11 software configuration above is not enough
to establish that it connects to this GNSS module. The 32.768 kHz crystal's
clock domain and the antenna connector's RF branch remain unassigned. No new
GPIO or clock register definitions follow from the component list alone.

## Configuration and validation boundary

`K10_ENABLE_SPI_NOR=0`, `K10_ENABLE_SI4732=0`, `K10_ENABLE_DISPLAY=0` and
`K10_ENABLE_KEYPAD=0` compile the corresponding APIs
as explicit `DISABLED` results with no register access. NOR, receiver and display
default to 1; keypad defaults to 0 for the PA14/SWCLK reason above. All operations
are blocking and intended for one caller outside an ISR.

Hardware work still needs the complete MCU/NOR suffixes and package markings,
board revisions, real core/bus clock, pull-ups, reset/power sequencing, and
receiver command traces. Delay accuracy follows the HAL's clock estimate;
`hal_clock_known()` records whether that clock is established. An inherited
bootloader clock estimate is not a measurement. The startup/receiver state also
matters: this first POWER_UP path expects the receiver to be available in its
powerdown state. The project has no confirmed external reset line for recovery
from an arbitrary previously running receiver firmware.

[puya-ds]: https://www.puyasemi.com/download_path/%E6%95%B0%E6%8D%AE%E6%89%8B%E5%86%8C/MCU%20%E5%BE%AE%E5%A4%84%E7%90%86%E5%99%A8/PY32F071%E7%B3%BB%E5%88%97%E6%95%B0%E6%8D%AE%E6%89%8B%E5%86%8C_V1.4.pdf
[puya-header]: https://github.com/OpenPuya/PY32F071_Firmware/blob/master/Drivers/CMSIS/Device/PY32F071/Include/py32f071xB.h
[puya-nor]: https://www.puyasemi.com/download_path/%E6%95%B0%E6%8D%AE%E6%89%8B%E5%86%8C/Flash/P25Q32H_Datasheet_V1.4.pdf
[gd-nor]: https://download.gigadevice.com/Datasheet/DS-00088-GD25Q32C-Rev4.1.pdf
[wb-nor]: https://devzone.nordicsemi.com/cfs-file/__key/communityserver-discussions-components-files/4/W25Q32JV-RevI-05042021-Plus.pdf
[an332]: https://www.skyworksinc.com/-/media/Skyworks/SL/documents/public/application-notes/AN332.pdf
[bk1080]: https://www.bekencorp.com/index/goods/detail/cid/34.html
[phy6235]: https://www.phyplusinc.com/product_detail/17.html
