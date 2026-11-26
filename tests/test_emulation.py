# SPDX-License-Identifier: MIT
"""Exercise emulator failure boundaries using independently authored Thumb code."""

import importlib.util
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
HAVE_UNICORN = importlib.util.find_spec("unicorn") is not None
if HAVE_UNICORN:
    from emulate_stock import (APP_BASE, FLASH_BASE, SCRATCH_BASE, EmulationError,
                               StockEmulator, NorModel)


@unittest.skipUnless(HAVE_UNICORN, "optional Unicorn dependency not installed")
class EmulatorBoundaryTests(unittest.TestCase):
    def test_independently_authored_add_and_stack_argument(self):
        # adds r0,r0,r1; bx lr. These four bytes are not from the vendor image.
        emulator = StockEmulator(bytes.fromhex("40187047"), require_reference=False)
        result = emulator.call(APP_BASE, (7, 9))
        self.assertEqual(result.r0, 16)
        self.assertEqual(result.hardware_hooks, 0)
        # ldr r0,[sp,#0]; bx lr
        emulator = StockEmulator(bytes.fromhex("00987047"), require_reference=False)
        self.assertEqual(emulator.call(APP_BASE, stack_args=(0xA55A,)).r0, 0xA55A)

    def test_hook_returns_and_records_explicit_arguments(self):
        emulator = StockEmulator(bytes.fromhex("7047"), require_reference=False)
        emulator.hook_function(APP_BASE, lambda machine: machine.reg(1) + 3, name="synthetic_hook")
        result = emulator.call(APP_BASE, (0, 39))
        self.assertEqual(result.r0, 42)
        self.assertEqual(result.hardware_hooks, 1)
        self.assertEqual(emulator.events[0]["function"], "synthetic_hook")
        self.assertEqual(emulator.events[0]["args"], [0, 39, 0, 0])

    def test_mmio_access_fails_instead_of_silently_returning_zero(self):
        # ldr r0,[r0]; bx lr
        emulator = StockEmulator(bytes.fromhex("00687047"), require_reference=False)
        with self.assertRaisesRegex(EmulationError, "0x40000000"):
            emulator.call(APP_BASE, (0x40000000,))

    def test_cpu_cannot_modify_firmware_or_execute_scratch(self):
        # str r1,[r0]; bx lr
        emulator = StockEmulator(bytes.fromhex("01607047"), require_reference=False)
        with self.assertRaisesRegex(EmulationError, "protected"):
            emulator.call(APP_BASE, (FLASH_BASE, 0))
        emulator.write(SCRATCH_BASE, bytes.fromhex("7047"))
        with self.assertRaisesRegex(EmulationError, "protected"):
            emulator.call(SCRATCH_BASE)

    def test_loop_has_instruction_budget_and_reference_digest_is_pinned(self):
        # b . (independent synthetic infinite loop)
        emulator = StockEmulator(bytes.fromhex("fee7"), require_reference=False)
        with self.assertRaisesRegex(EmulationError, "within limits"):
            emulator.call(APP_BASE, limit=30)
        self.assertLessEqual(emulator.instructions, 30)
        with self.assertRaisesRegex(EmulationError, "SHA-256"):
            StockEmulator(bytes.fromhex("7047"))
        with self.assertRaisesRegex(EmulationError, "reference"):
            emulator.initialize_data()

    def test_nor_model_enforces_one_to_zero_page_and_capacity_boundaries(self):
        emulator = StockEmulator(bytes.fromhex("7047"), require_reference=False)
        nor = NorModel(4096)
        nor.attach(emulator)
        emulator.write(SCRATCH_BASE, b"\x0f\xf0")
        emulator.call(0x080197EC, (SCRATCH_BASE, 0x100, 2))
        emulator.write(SCRATCH_BASE, b"\xff\xff")
        emulator.call(0x080197EC, (SCRATCH_BASE, 0x100, 2))
        self.assertEqual(nor.data[0x100:0x102], b"\x0f\xf0")
        with self.assertRaisesRegex(EmulationError, "crossed a page"):
            emulator.call(0x080197EC, (SCRATCH_BASE, 0x1FF, 2))
        with self.assertRaisesRegex(EmulationError, "out of range"):
            emulator.call(0x080195E4, (4095, SCRATCH_BASE, 2))
        emulator.call(0x08019588, (0x100, 0))
        self.assertEqual(nor.data, b"\xff" * 4096)


@unittest.skipUnless(HAVE_UNICORN and os.environ.get("K10_REFERENCE_RAW"),
                     "optional Unicorn and exact local stock raw image required")
class ReferenceCpuTests(unittest.TestCase):
    def test_scatter_storage_and_rf_gpio_replay(self):
        import hashlib
        from emulate_storage import run_storage
        from emulate_rf_bus import run_rf_bus
        raw = Path(os.environ["K10_REFERENCE_RAW"]).read_bytes()
        emulator = StockEmulator(raw)
        emulator.initialize_data()
        self.assertEqual(hashlib.sha256(emulator.read(0x20000000, 0x4C4)).hexdigest(),
                         "64ac4a1654da0883255d522224c2eba6922d204917a5302de5d2f1d27894245d")
        storage = run_storage(raw)
        self.assertEqual(storage["passed"], 8)
        # Stored experiment logs must not be aliases to a later cleared list.
        self.assertEqual([item["op"] for item in storage["cases"][0]["operations"]],
                         ["read", "program", "program"])
        self.assertEqual(storage["cases"][1]["operations"], [])
        self.assertEqual(run_rf_bus(raw)["passed"], 7)


if __name__ == "__main__":
    unittest.main()
