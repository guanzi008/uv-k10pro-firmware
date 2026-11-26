# SPDX-License-Identifier: MIT
"""Compile the real C LCD/PWM driver against host MMIO and fault scenarios."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ("not-initialized", "normal", "stale-rx", "init-timeout", "txe-timeout",
             "frozen-tick", "rxne-timeout", "bsy-timeout", "spi-fault")


class DisplayDriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build = ROOT / "build"
        build.mkdir(exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="host-display-", dir=build)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.enabled = Path(cls.temporary.name) / "enabled"
        cls.disabled = Path(cls.temporary.name) / "disabled"
        common = [os.environ.get("HOST_CC", "cc"), "-std=c11", "-O1", "-g",
                  "-Wall", "-Wextra", "-Werror", "-Wshadow", "-Wstrict-prototypes",
                  "-fsanitize=undefined", "-fno-sanitize-recover=all",
                  "-Itests/display_mocks", "-Itests/mocks", "-Ifirmware/include",
                  "tests/test_display_driver.c", "firmware/src/display.c"]
        subprocess.run(common + ["-o", str(cls.enabled)], cwd=ROOT, check=True, timeout=30)
        subprocess.run(common + ["-DK10_ENABLE_DISPLAY=0", "-o", str(cls.disabled)],
                       cwd=ROOT, check=True, timeout=30)

    def test_real_driver_initialization_pixels_bounds_and_faults(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                result = subprocess.run([str(self.enabled), scenario], cwd=ROOT,
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_disabled_build_has_no_mmio(self):
        result = subprocess.run([str(self.disabled), "disabled"], cwd=ROOT,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
