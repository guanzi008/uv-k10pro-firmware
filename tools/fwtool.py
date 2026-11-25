#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Pack, unpack, or inspect the observed Quansheng update-file container.

Independently implemented from the observed format. The protocol XOR table is
also documented by Armel F4HWN's MIT-licensed _fwcodec.py (Copyright (c) 2026):
https://github.com/armel/uv-k1-k5v3-firmware-custom/blob/main/tools/serialtool/_fwcodec.py

This module only reads and writes local files; it has no radio transport code.
"""

import argparse
import binascii
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
import sys

from verify_image import APP_BASE, APP_LIMIT, DEVICE_FLASH_END, verify_image

VERSION_OFFSET = 0x2000
VERSION_SIZE = 16
CRC_SIZE = 2
# Public same-protocol tooling requires bytes beyond the inserted version field.
# A whole extra word preserves alignment without claiming an updater requirement.
MIN_PACKED_RAW_BYTES = VERSION_OFFSET + 4
MAX_CONTAINER_BYTES = DEVICE_FLASH_END - APP_BASE + VERSION_SIZE + CRC_SIZE
XOR_TABLE = bytes.fromhex(
    "47 22 C0 52 5D 57 48 94 B1 60 60 DB 6F E3 4C 7C "
    "D8 4A D6 8B 30 EC 25 E0 4C D9 00 7F BF E3 54 05 "
    "E9 3A 97 6B B0 6E 0C FB B1 1A E2 C9 C1 56 47 E9 "
    "BA F1 42 B6 67 5F 0F 96 F7 C9 3C 84 1B 26 E1 4E "
    "3B 6F 66 E6 A0 6A B0 BF C6 A5 70 3A BA 18 9E 27 "
    "1A 53 5B 71 B1 94 1E 18 F2 D6 81 02 22 FD 5A 28 "
    "91 DB BA 5D 64 C6 FE 86 83 9C 50 1C 73 03 11 D6 "
    "AF 30 F4 2C 77 B2 7D BB 3F 29 28 57 22 D6 92 8B"
)


class FirmwareError(ValueError):
    """The container or requested version is invalid."""


@dataclass(frozen=True)
class DecodedFirmware:
    raw: bytes
    version: str
    crc16: int


def version_field(version: str) -> bytes:
    """Encode 1..15 printable ASCII bytes and zero-fill the 16-byte field."""
    try:
        encoded = version.encode("ascii")
    except UnicodeEncodeError as error:
        raise FirmwareError("version must contain printable ASCII only") from error
    if not 1 <= len(encoded) <= 15 or any(not 0x20 <= byte <= 0x7E for byte in encoded):
        raise FirmwareError("version must contain 1..15 printable ASCII characters")
    return encoded.ljust(VERSION_SIZE, b"\0")


def xor_body(body: bytes) -> bytes:
    return bytes(byte ^ XOR_TABLE[offset % len(XOR_TABLE)] for offset, byte in enumerate(body))


def pack_firmware(raw: bytes, version: str, *, app_limit: int = APP_LIMIT) -> bytes:
    """Validate and package an application, preserving all input bytes.

    Short images are extended with erased-flash bytes (0xFF) to length 0x2004.
    The original unpadded length is not represented by the vendor format.
    The optional limit exists for explicit reference-file analysis; the CLI
    always uses this project's conservative flash boundary.
    """
    verify_image(raw, app_limit=app_limit)
    field = version_field(version)
    padded = raw.ljust(MIN_PACKED_RAW_BYTES, b"\xff")
    body = xor_body(padded[:VERSION_OFFSET] + field + padded[VERSION_OFFSET:])
    return body + struct.pack("<H", binascii.crc_hqx(body, 0))


def unpack_firmware(container: bytes) -> DecodedFirmware:
    """Require a valid CRC, version field, and plausible application vectors."""
    minimum = VERSION_OFFSET + VERSION_SIZE + CRC_SIZE
    if not minimum <= len(container) <= MAX_CONTAINER_BYTES:
        raise FirmwareError(f"container length must be {minimum}..{MAX_CONTAINER_BYTES} bytes")
    body = container[:-CRC_SIZE]
    stored_crc = struct.unpack_from("<H", container, len(body))[0]
    calculated_crc = binascii.crc_hqx(body, 0)
    if stored_crc != calculated_crc:
        raise FirmwareError(
            f"CRC16/XMODEM mismatch: stored 0x{stored_crc:04x}, calculated 0x{calculated_crc:04x}"
        )
    decoded = xor_body(body)
    field = decoded[VERSION_OFFSET:VERSION_OFFSET + VERSION_SIZE]
    text, separator, padding = field.partition(b"\0")
    if not separator or any(padding):
        raise FirmwareError("version field must be NUL terminated and zero padded")
    try:
        version = text.decode("ascii")
    except UnicodeDecodeError as error:
        raise FirmwareError("version field is not ASCII") from error
    if version_field(version) != field:
        raise FirmwareError("invalid version field")
    raw = decoded[:VERSION_OFFSET] + decoded[VERSION_OFFSET + VERSION_SIZE:]
    verify_image(raw, app_limit=DEVICE_FLASH_END)
    return DecodedFirmware(raw=raw, version=version, crc16=stored_crc)


def container_report(container: bytes) -> dict:
    decoded = unpack_firmware(container)
    return {
        "format": "quansheng-xor128-crc16xmodem",
        "container_bytes": len(container),
        "container_sha256": hashlib.sha256(container).hexdigest(),
        "version": decoded.version,
        "crc16_xmodem": f"0x{decoded.crc16:04x}",
        "crc_valid": True,
        "raw": verify_image(decoded.raw, app_limit=DEVICE_FLASH_END),
        "fits_project_flash_limit": len(decoded.raw) <= APP_LIMIT - APP_BASE,
    }


def write_output(path: Path, data: bytes, *, force: bool) -> None:
    with path.open("wb" if force else "xb") as output:
        output.write(data)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pack = commands.add_parser("pack", help="validate and package a custom raw image")
    pack.add_argument("input", type=Path)
    pack.add_argument("output", type=Path)
    pack.add_argument("--version", required=True, help="1..15 printable ASCII characters")
    pack.add_argument("--force", action="store_true", help="replace an existing output file")
    unpack = commands.add_parser("unpack", help="verify a container and extract its raw image")
    unpack.add_argument("input", type=Path)
    unpack.add_argument("output", type=Path)
    unpack.add_argument("--force", action="store_true", help="replace an existing output file")
    inspect = commands.add_parser("inspect", help="report checksums, vectors, and container metadata")
    inspect.add_argument("input", type=Path)
    inspect.add_argument("--raw", action="store_true", help="inspect a raw image within the full 120 KiB extent")
    args = parser.parse_args(argv)
    try:
        data = args.input.read_bytes()
        if args.command in ("pack", "unpack") and args.input.resolve() == args.output.resolve():
            raise FirmwareError("input and output must be different files")
        if args.command == "pack":
            result = pack_firmware(data, args.version)
            write_output(args.output, result, force=args.force)
            report = container_report(result)
            report["original_raw_bytes"] = len(data)
            report["padding_bytes"] = max(MIN_PACKED_RAW_BYTES - len(data), 0)
        elif args.command == "unpack":
            result = unpack_firmware(data)
            write_output(args.output, result.raw, force=args.force)
            report = container_report(data)
        elif args.raw:
            report = verify_image(data, app_limit=DEVICE_FLASH_END)
        else:
            report = container_report(data)
    except (OSError, ValueError) as error:
        print(f"fwtool: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
