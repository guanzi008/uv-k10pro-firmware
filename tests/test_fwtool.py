# SPDX-License-Identifier: MIT
"""Synthetic, dependency-free tests; optional stock sample stays outside git."""

import binascii
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import fwtool  # noqa: E402
import verify_image  # noqa: E402


def synthetic_image(size=512):
    image = bytearray(b"\xff" * size)
    vectors = [0] * 48
    vectors[0] = verify_image.SRAM_END
    vectors[1] = verify_image.APP_BASE + verify_image.VECTOR_BYTES + 1
    struct.pack_into("<48I", image, 0, *vectors)
    image[192:194] = b"\xfe\xe7"  # Thumb branch-to-self; never executed by these tests.
    return bytes(image)


class CodecTests(unittest.TestCase):
    def test_crc_standard_check_value(self):
        self.assertEqual(binascii.crc_hqx(b"123456789", 0), 0x31C3)

    def test_short_image_is_padded_without_loss(self):
        raw = synthetic_image()
        packed = fwtool.pack_firmware(raw, "0.1.0-exp")
        decoded = fwtool.unpack_firmware(packed)
        self.assertEqual(len(packed), 0x2004 + 18)
        self.assertEqual(decoded.raw, raw.ljust(0x2004, b"\xff"))
        self.assertEqual(decoded.version, "0.1.0-exp")
        self.assertEqual(fwtool.pack_firmware(decoded.raw, decoded.version), packed)

    def test_boundary_roundtrip_preserves_erased_tail(self):
        for size in (0x2004, 0x2005, verify_image.APP_LIMIT - verify_image.APP_BASE):
            with self.subTest(size=size):
                raw = synthetic_image(size)
                self.assertEqual(fwtool.unpack_firmware(fwtool.pack_firmware(raw, "v1")).raw, raw)

    def test_corrupt_body_or_crc_rejected(self):
        packed = fwtool.pack_firmware(synthetic_image(), "v1")
        for offset in (0, 500, 0x2000, len(packed) - 1):
            with self.subTest(offset=offset):
                damaged = bytearray(packed)
                damaged[offset] ^= 0x80
                with self.assertRaisesRegex(fwtool.FirmwareError, "CRC16"):
                    fwtool.unpack_firmware(bytes(damaged))

    def test_invalid_version_input(self):
        for version in ("", "x" * 16, "汉字", "a\0b", "a\nb", "a\x7fb"):
            with self.subTest(version=version), self.assertRaises(fwtool.FirmwareError):
                fwtool.pack_firmware(synthetic_image(), version)
        self.assertEqual(len(fwtool.version_field("x" * 15)), 16)

    def test_rechecks_decoded_field_after_valid_crc(self):
        packed = fwtool.pack_firmware(synthetic_image(), "v1")
        plain = bytearray(fwtool.xor_body(packed[:-2]))
        for field in (b"x" * 16, b"v\0z" + b"\0" * 13, b"\xff\0" + b"\0" * 14):
            with self.subTest(field=field):
                plain[0x2000:0x2010] = field
                body = fwtool.xor_body(plain)
                invalid = body + struct.pack("<H", binascii.crc_hqx(body, 0))
                with self.assertRaises(fwtool.FirmwareError):
                    fwtool.unpack_firmware(invalid)

    def test_pack_enforces_conservative_boundary(self):
        raw = synthetic_image(verify_image.APP_LIMIT - verify_image.APP_BASE + 1)
        with self.assertRaises(verify_image.VerificationError):
            fwtool.pack_firmware(raw, "v1")
        reference = fwtool.pack_firmware(raw, "v1", app_limit=verify_image.DEVICE_FLASH_END)
        self.assertFalse(fwtool.container_report(reference)["fits_project_flash_limit"])

    def test_bad_container_lengths(self):
        for length in (0, 2, 0x2000 + 17, fwtool.MAX_CONTAINER_BYTES + 1):
            with self.subTest(length=length), self.assertRaises(fwtool.FirmwareError):
                fwtool.unpack_firmware(b"\0" * length)

    def test_cli_roundtrip_and_no_implicit_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "raw.bin"
            packed = Path(directory) / "packed.bin"
            extracted = Path(directory) / "extracted.bin"
            raw.write_bytes(synthetic_image(0x2100))
            command = [sys.executable, str(TOOLS / "fwtool.py"), "pack", str(raw), str(packed), "--version", "test"]
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertTrue(json.loads(result.stdout)["crc_valid"])
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 1)
            subprocess.run(command + ["--force"], capture_output=True, check=True)
            subprocess.run([sys.executable, str(TOOLS / "fwtool.py"), "unpack", str(packed), str(extracted)],
                           capture_output=True, check=True)
            self.assertEqual(extracted.read_bytes(), raw.read_bytes())

    @unittest.skipUnless(os.environ.get("K10_REFERENCE_FIRMWARE"), "optional official reference path not supplied")
    def test_official_reference_exact_roundtrip(self):
        packed = Path(os.environ["K10_REFERENCE_FIRMWARE"]).read_bytes()
        self.assertEqual(hashlib.sha256(packed).hexdigest(),
                         "aa33d01a06c5f013ef46afb6c6a37dd16b15318d0e6409bbadfaba53bdb5ad5b")
        decoded = fwtool.unpack_firmware(packed)
        self.assertEqual(decoded.version, "8.02.16")
        self.assertEqual(decoded.crc16, 0xC486)
        self.assertEqual(len(decoded.raw), 118956)
        self.assertEqual(hashlib.sha256(decoded.raw).hexdigest(),
                         "061759672cc69a1b00420ce6fae67b0756d3db111f8fa4329cadc940dcd17f17")
        self.assertEqual(fwtool.pack_firmware(decoded.raw, decoded.version,
                                             app_limit=verify_image.DEVICE_FLASH_END), packed)


if __name__ == "__main__":
    unittest.main()
