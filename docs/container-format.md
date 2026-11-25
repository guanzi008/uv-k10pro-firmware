# Firmware container and host tools

The official UV-K10 Pro English `8.02.16` download uses a small update-file
container around a raw ARM application. The implementation in `tools/fwtool.py`
is independent Python code. The protocol constants and format are also publicly
documented by [Armel F4HWN's MIT-licensed codec](https://github.com/armel/uv-k1-k5v3-firmware-custom/blob/main/tools/serialtool/_fwcodec.py).
That shared container does **not** establish application compatibility with other
Quansheng models.

## Observed file format

Given a raw application `R` and an ASCII version `V`:

1. Insert a 16-byte, NUL-terminated and zero-padded version field at raw file
   offset `0x2000`: `R[:0x2000] + V + R[0x2000:]`.
2. XOR every byte with the 128-byte repeating protocol table in `fwtool.py`,
   starting with table byte zero at file byte zero. The table continues through
   the inserted version field; it is not reset afterward.
3. Calculate CRC16/XMODEM over the **obfuscated body**, with initial value zero,
   polynomial `0x1021`, and no final XOR.
4. Append that CRC as a two-byte **little-endian** integer.

The container adds 18 bytes. There is no original-length field or cryptographic
signature in this observed container structure. A matching CRC detects ordinary
file corruption; it does not establish authenticity or hardware compatibility.

For a custom image shorter than `0x2004` bytes, this tool first extends it to that
length with `0xFF`. This includes one aligned word beyond the insertion point;
the public codec linked above rejects containers without any bytes after that
field. This padding policy does not establish the manufacturer's bootloader
minimum length. Unpacking preserves these bytes, because the format provides
no reliable way to distinguish padding from actual application data. It never
trims trailing `0xFF` from a supplied image.

## Application boundaries

| Property | Value | Basis |
| --- | --- | --- |
| Application base | `0x08002000` | Stock vector targets and VTOR write |
| Raw vector table | 48 little-endian words | 16 core entries + 32 IRQ entries |
| SRAM range | `0x20000000..0x20003FFF` | PY32F071-compatible register and memory evidence |
| Initial SP accepted | Greater than SRAM base, at most `0x20004000`, aligned to 8 bytes | Descending-stack convention and project checks |
| Custom image end, exclusive | At most `0x0801F000` | Conservative project allocation, 116 KiB |
| Reference inspection end, exclusive | At most `0x08020000` | Full 120 KiB extent above the observed application base |
| Reserved IRQ30 | Vector index 46 must be zero | Observed stock image and PY32F071 layout |

The top 4 KiB reservation is a project precaution while the complete device map
is still unverified. It is **not** a claim that the entire region contains
calibration data: the inspected stock application actually reaches
`0x0801F0AC`, 172 bytes beyond this project's conservative boundary.
The linker boundary does not control an external updater's erase range; that
behavior has not been verified.

The verifier also checks the Cortex-M0+ reserved core vectors and requires every
nonzero handler to have the Thumb bit and to point past the vector table into
the supplied image. It checks layout, not instruction semantics, bootloader
behavior, peripheral wiring, or successful operation on a radio.

## Commands

Run these commands from the repository root with Python 3.9 or newer. No external
Python packages, serial ports, or attached radio are needed.

```sh
python3 tools/verify_image.py build/k10pro.bin
python3 tools/fwtool.py pack build/k10pro.bin build/k10pro.packed.bin --version 0.1.0-exp
python3 tools/fwtool.py inspect build/k10pro.packed.bin
python3 tools/fwtool.py unpack build/k10pro.packed.bin build/extracted.bin
python3 tools/fwtool.py inspect build/extracted.bin --raw
python3 -m unittest discover -s tests -v
```

Output files are not overwritten unless `--force` is supplied. Input and output
must be different paths. Every command exits nonzero on a failed check. The
`pack` command enforces the conservative custom-image limit; `inspect --raw` and
`unpack` allow the full reference extent so that the original stock sample can
be analyzed. `verify_image.py` checks custom builds only.

## Pinned reference evidence

The reference file was obtained from the manufacturer's
[official download record](https://cms.qsfj.com/api/entries/files/11434?fl-language=en)
for [UV-K10 Pro](https://qsfj.com/products/11145). It is not redistributed in this
repository.

| Item | Observed value |
| --- | --- |
| Filename | `K10PRO_ENG_v8.02.16_publish.bin` |
| Container length | 118,974 bytes |
| Container SHA-256 | `aa33d01a06c5f013ef46afb6c6a37dd16b15318d0e6409bbadfaba53bdb5ad5b` |
| Version field | `8.02.16` |
| CRC16/XMODEM | `0xC486` |
| Raw length | 118,956 bytes |
| Raw SHA-256 | `061759672cc69a1b00420ce6fae67b0756d3db111f8fa4329cadc940dcd17f17` |

An optional test checks that exact independently supplied reference and verifies
byte-for-byte decode/repack identity:

```sh
K10_REFERENCE_FIRMWARE=/path/outside/repo/K10PRO_ENG_v8.02.16_publish.bin \
  python3 -m unittest discover -s tests -v
```

The normal test suite uses synthetic images and does not download proprietary
firmware. It covers CRC corruption, malformed version fields, vector bounds,
reserved vectors, stack alignment, padding, maximum image extent, CLI failure
behavior, and overwrite protection.
