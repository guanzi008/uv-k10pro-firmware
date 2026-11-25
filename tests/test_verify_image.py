# SPDX-License-Identifier: MIT
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import verify_image  # noqa: E402


def synthetic_image(size=512, *, sp=0x20004000, reset=0x080020C1):
    vectors = [0] * 48
    vectors[0] = sp
    vectors[1] = reset
    return struct.pack("<48I", *vectors) + b"\xff" * (size - 192)


class ImageChecks(unittest.TestCase):
    def test_valid_layout(self):
        report = verify_image.verify_image(synthetic_image())
        self.assertEqual(report["initial_sp"], "0x20004000")
        self.assertEqual(report["image_end_exclusive"], "0x08002200")

    def test_stack_pointer_constraints(self):
        for sp in (0, 0x10000000, 0x20000000, 0x20004008, 0x20003FFC):
            with self.subTest(sp=sp), self.assertRaisesRegex(verify_image.VerificationError, "SP"):
                verify_image.verify_image(synthetic_image(sp=sp))

    def test_vector_thumb_bit_and_code_extent(self):
        for reset in (0, 0x080020C0, 0x08002001, 0x08000001, 0x08002201, 0x20000101):
            with self.subTest(reset=reset), self.assertRaises(verify_image.VerificationError):
                verify_image.verify_image(synthetic_image(reset=reset))

    def test_checks_every_nonzero_handler(self):
        image = bytearray(synthetic_image())
        struct.pack_into("<I", image, 47 * 4, 0x08003001)
        with self.assertRaisesRegex(verify_image.VerificationError, "vector 47"):
            verify_image.verify_image(image)

    def test_reserved_core_and_irq30_must_be_zero(self):
        for index in verify_image.RESERVED_VECTORS:
            image = bytearray(synthetic_image())
            struct.pack_into("<I", image, index * 4, 0x080020C1)
            with self.subTest(index=index), self.assertRaisesRegex(verify_image.VerificationError, "reserved"):
                verify_image.verify_image(image)

    def test_image_extent_and_vector_table_size(self):
        for image in (b"", b"\0" * 191, synthetic_image(verify_image.APP_LIMIT - verify_image.APP_BASE + 1)):
            with self.subTest(size=len(image)), self.assertRaises(verify_image.VerificationError):
                verify_image.verify_image(image)

    def test_cli_returns_failure_for_invalid_image(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.bin"
            path.write_bytes(synthetic_image())
            command = [sys.executable, str(TOOLS / "verify_image.py"), str(path)]
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout)["structural_checks"], "passed")
            path.write_bytes(b"broken")
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("complete", result.stderr)


if __name__ == "__main__":
    unittest.main()
