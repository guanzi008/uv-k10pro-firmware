# SPDX-License-Identifier: MIT
"""Reject unsupported input models and optionally replay the exact stock image."""

import importlib.util
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
HAVE_UNICORN = importlib.util.find_spec("unicorn") is not None
if HAVE_UNICORN:
    from emulate_stock import APP_BASE, EmulationError, StockEmulator
    from emulate_keypad import KeypadGpioModel, run_keypad


@unittest.skipUnless(HAVE_UNICORN, "optional Unicorn dependency not installed")
class KeypadModelBoundaryTests(unittest.TestCase):
    def test_idr_requires_explicit_input_pull_configuration(self):
        # Independently authored ldr r0,[r0]; bx lr, not vendor instructions.
        em = StockEmulator(bytes.fromhex("00687047"), require_reference=False)
        KeypadGpioModel(em)
        with self.assertRaisesRegex(EmulationError, "input/pull-up"):
            em.call(APP_BASE, (0x50000410,))

    def test_unmodeled_gpio_register_does_not_silently_read_zero(self):
        em = StockEmulator(bytes.fromhex("00687047"), require_reference=False)
        KeypadGpioModel(em)
        with self.assertRaisesRegex(EmulationError, "unmodeled GPIO"):
            em.call(APP_BASE, (0x50000C10,))  # Port D is outside this input model.

    def test_unknown_keys_and_unmodeled_contention_are_rejected(self):
        em = StockEmulator(bytes.fromhex("7047"), require_reference=False)
        gpio = KeypadGpioModel(em)
        with self.assertRaisesRegex(ValueError, "not scanner keys"):
            gpio.set_pressed((21,))  # PTT is a separate PC13 argument.
        with self.assertRaisesRegex(ValueError, "electrical model"):
            gpio.set_pressed((1, 2))  # Different driven rows, same column.
        with self.assertRaisesRegex(ValueError, "direct switch"):
            gpio.set_pressed((24, 17))


@unittest.skipUnless(HAVE_UNICORN and os.environ.get("K10_REFERENCE_RAW"),
                     "optional Unicorn and exact local stock raw image required")
class KeypadReferenceTests(unittest.TestCase):
    def test_original_scan_filter_and_ui_dataflow(self):
        report = run_keypad(Path(os.environ["K10_REFERENCE_RAW"]).read_bytes())
        self.assertEqual(report["passed"], 53)
        idle = report["scan_cases"][0]
        self.assertEqual(idle["gpio_reads"], 22)
        self.assertEqual(idle["row_low_order"], ["PF8", "PB4", "PB3", "PA14"])
        transition = next(case for case in report["filter_cases"]
                          if case["name"] == "stable_key_transition")
        self.assertEqual([(event["key"], event["pressed"]) for event in transition["events"]],
                         [(1, 1), (1, 0), (2, 1)])
        mapping = report["ui_cases"][0]
        self.assertEqual(mapping["characters"][:10], "0123456789")


if __name__ == "__main__":
    unittest.main()
