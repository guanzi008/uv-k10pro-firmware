# SPDX-License-Identifier: MIT
"""Test display MMIO boundaries and optional exact-image CPU experiments."""

import importlib.util
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
HAVE_UNICORN = importlib.util.find_spec("unicorn") is not None
if HAVE_UNICORN:
    from emulate_stock import APP_BASE, EmulationError, StockEmulator
    from emulate_display import DisplayMMIO, GPIO, SPI1, run_experiments


@unittest.skipUnless(HAVE_UNICORN, "optional Unicorn dependency not installed")
class DisplayBoundaryTests(unittest.TestCase):
    def test_gpio_latches_and_spi_records_use_executed_mmio(self):
        # Independently authored instructions: str r1,[r0]; bx lr;
        # strb r1,[r0]; bx lr. No vendor bytes are part of this test.
        em = StockEmulator(bytes.fromhex("0160704701707047"), require_reference=False)
        bus = DisplayMMIO(em)
        em.call(APP_BASE, (GPIO["B"] + 0x18, 4))
        self.assertEqual(bus.level("B", 2), 1)
        em.call(APP_BASE, (GPIO["B"] + 0x28, 4))
        em.call(APP_BASE, (GPIO["F"] + 0x18, 4))
        em.call(APP_BASE + 4, (SPI1 + 0x0C, 0xA5))
        self.assertEqual(bus.spi_bytes, [{"value": 0xA5, "cs": 0, "dc": 1, "transaction": 1}])
        em.call(APP_BASE, (GPIO["B"] + 0x18, 4))
        em.call(APP_BASE, (GPIO["B"] + 0x28, 4))
        em.call(APP_BASE + 4, (SPI1 + 0x0C, 0x5A))
        self.assertEqual(len(bus.segments()), 2, "CS boundaries must split equal D/C segments")

    def test_reserved_register_and_wrong_spi_width_fail_closed(self):
        em = StockEmulator(bytes.fromhex("01607047"), require_reference=False)
        DisplayMMIO(em)
        with self.assertRaisesRegex(EmulationError, "unmodeled display MMIO"):
            em.call(APP_BASE, (SPI1 + 0x18, 0))
        with self.assertRaisesRegex(EmulationError, "not one byte"):
            em.call(APP_BASE, (SPI1 + 0x0C, 0))


@unittest.skipUnless(HAVE_UNICORN and os.environ.get("K10_REFERENCE_RAW"),
                     "optional Unicorn and exact local stock raw image required")
class DisplayReferenceTests(unittest.TestCase):
    def test_stock_init_page_stream_partial_clear_and_pwm(self):
        result = run_experiments(Path(os.environ["K10_REFERENCE_RAW"]).read_bytes())
        self.assertEqual(result["passed"], 14)
        self.assertEqual(sum(result["scenario_counts"].values()), 14)
        self.assertEqual(result["lcd"]["spi"]["cr1"], "0x0000035f")
        self.assertEqual(result["lcd"]["initialization_byte_count"], 1067)
        self.assertEqual(result["lcd"]["initialization_segments"][0]["length"], 17)
        self.assertEqual([p["af"] for p in result["backlight"]["pins_in_argument_order"]], [11, 2, 1])
        self.assertEqual(result["backlight"]["settings_apply"]["compare"], [325, 1175, 2225])
        self.assertEqual(result["backlight"]["settings_to_pwm"]["cases"][-1]["loaded"], [50, 50, 100])


if __name__ == "__main__":
    unittest.main()
