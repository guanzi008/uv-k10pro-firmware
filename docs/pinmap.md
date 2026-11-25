# Board interfaces recovered from the application

This is a **firmware-derived board profile**, not a checked schematic or a report
of successful operation on a physical radio. It targets the interface used by
`K10PRO_ENG_v8.02.16`. Board revisions and optional components still need to be
identified on hardware.

The independently implemented drivers use only the pins below. Unknown reset,
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
| MISO | PA1 | Alternate function 9 | Same routine sets pin mask `6`, AF9 for PA1/PA2 |
| MOSI | PA2 | Alternate function 9 | Same pair; signal names match Puya table 3-3 |
| CS# | PA3 | GPIO output, active low | `0x080195E4` writes bit3 to GPIOA BRR; completion writes BSRR |
| Controller | SPI2 | Base `0x40003800` | Byte transfer at `0x0801A504` uses SR and DR at offsets 8 and 12 |

The GPIO initializer called at `0x0801B040` establishes the parameter layout, so
the AF numbers are traced through the call rather than inferred from a familiar
STM32 pinout. [Puya's PY32F071 datasheet, table 3-3][puya-ds] confirms PA0/AF8 as
SPI2_SCK and PA1/PA2 AF9 as SPI2_MISO/SPI2_MOSI.

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

This table is **not identification of the chip fitted to a UV-K10 Pro**. An
unknown but readable ID is returned unchanged, capacity remains zero, and array
reads return `SPI_NOR_UNSUPPORTED_ID`. Add other IDs only with their supporting
datasheet, or implement and validate SFDP parsing. The read-only interface has no
program, erase, write-enable, status-write, unlock or patch-upload operation. It
does not wake a chip left in an undocumented deep-sleep or QPI state.

## Optional Si4732 receiver

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

## Configuration and validation boundary

`K10_ENABLE_SPI_NOR=0` and `K10_ENABLE_SI4732=0` compile the corresponding APIs
as explicit `DISABLED` results with no register access. They default to 1 for
this experimental board profile because their bus pins are established from the
sample. All operations are blocking and intended for one caller outside an ISR.

Hardware work still needs to verify the fitted MCU and NOR markings, optional
receiver population, real core/bus clock, pull-ups, reset/power sequencing, and
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
