#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check structural properties of this project's raw Cortex-M0+ image.

This is a host-side check, not a substitute for hardware validation. It never
opens a serial port and cannot flash a radio.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

APP_BASE = 0x08002000
APP_LIMIT = 0x0801F000  # Reserve the top 4 KiB in this project's linked image.
DEVICE_FLASH_END = 0x08020000
SRAM_BASE = 0x20000000
SRAM_END = 0x20004000
VECTOR_COUNT = 48
VECTOR_BYTES = VECTOR_COUNT * 4
RESERVED_VECTORS = (4, 5, 6, 7, 8, 9, 10, 12, 13, 46)


class VerificationError(ValueError):
    """A binary is incompatible with the expected application layout."""


def verify_image(raw: bytes, *, app_limit: int = APP_LIMIT) -> dict:
    """Validate a raw image and return JSON-serializable diagnostic metadata.

    ``app_limit`` may be DEVICE_FLASH_END when inspecting a stock reference.
    The CLI deliberately uses the conservative project limit.
    """
    if not APP_BASE < app_limit <= DEVICE_FLASH_END:
        raise VerificationError("invalid application flash limit")
    if len(raw) < VECTOR_BYTES:
        raise VerificationError(f"image needs a complete {VECTOR_BYTES}-byte vector table")
    if len(raw) > app_limit - APP_BASE:
        raise VerificationError(
            f"image is {len(raw)} bytes; application limit is {app_limit - APP_BASE} bytes"
        )

    vectors = struct.unpack_from("<48I", raw)
    sp = vectors[0]
    if sp % 8 or not SRAM_BASE < sp <= SRAM_END:
        raise VerificationError(
            f"initial SP 0x{sp:08x} must be 8-byte aligned within 16 KiB SRAM"
        )
    if vectors[1] == 0:
        raise VerificationError("reset vector must not be zero")
    for index in RESERVED_VECTORS:
        if vectors[index] != 0:
            raise VerificationError(f"reserved vector {index} must be zero")

    image_end = APP_BASE + len(raw)
    for index, pointer in enumerate(vectors[1:], start=1):
        if pointer == 0:
            continue
        target = pointer & ~1
        if not pointer & 1:
            raise VerificationError(f"vector {index} lacks the Thumb state bit")
        if not APP_BASE + VECTOR_BYTES <= target or target + 2 > image_end:
            raise VerificationError(
                f"vector {index} target 0x{target:08x} lies outside image code"
            )

    return {
        "format": "raw-cortex-m0plus-application",
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "application_base": f"0x{APP_BASE:08x}",
        "image_end_exclusive": f"0x{image_end:08x}",
        "checked_flash_limit": f"0x{app_limit:08x}",
        "initial_sp": f"0x{sp:08x}",
        "reset_vector": f"0x{vectors[1]:08x}",
        "vector_count": VECTOR_COUNT,
        "reserved_irq30_zero": True,
        "structural_checks": "passed",
        "hardware_validation": "not performed by this tool",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="raw .bin from this project's linker layout")
    args = parser.parse_args(argv)
    try:
        report = verify_image(args.image.read_bytes())
    except (OSError, VerificationError) as error:
        print(f"verify_image: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
