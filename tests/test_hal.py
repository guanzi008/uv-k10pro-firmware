# SPDX-License-Identifier: MIT
"""Run production HAL code against deterministic host MMIO fixtures."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class HalClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shlex.split(os.environ.get("HOST_CC", "cc"))
        if not compiler or shutil.which(compiler[0]) is None:
            raise unittest.SkipTest("Host C compiler unavailable; set HOST_CC to run HAL tests")
        cls.output_dir = tempfile.TemporaryDirectory(prefix="k10-hal-tests-")
        cls.addClassCleanup(cls.output_dir.cleanup)
        cls.executables = {}
        for variant, flags in (
            ("default", []),
            ("external", ["-DK10_HSE_HZ=12000000", "-DK10_LSE_HZ=32000"]),
        ):
            executable = Path(cls.output_dir.name) / f"hal-{variant}"
            command = compiler + [
                "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                "-Wshadow", "-Wundef", "-Wstrict-prototypes",
                "-I", str(ROOT / "tests" / "hal_mocks"),
                "-I", str(ROOT / "firmware" / "include"),
                *flags,
                str(ROOT / "tests" / "test_hal_clock.c"),
                "-o", str(executable),
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise RuntimeError(f"HAL host build failed:\n{result.stdout}{result.stderr}")
            cls.executables[variant] = executable

    def run_case(self, case, variant="default"):
        result = subprocess.run(
            [str(self.executables[variant]), case],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"PASS: HAL {case}", result.stdout)

    def test_hsi_pll_and_prescalers(self):
        self.run_case("internal")

    def test_reserved_unready_and_too_slow_clocks_are_unknown(self):
        self.run_case("invalid")

    def test_external_defaults_are_assumptions(self):
        self.run_case("external")

    def test_explicit_external_frequencies_are_used(self):
        self.run_case("external", "external")

    def test_clock_switch_during_snapshot_is_unknown(self):
        self.run_case("transition")

    def test_systick_wraparound_delay_and_idempotence(self):
        self.run_case("timer")


if __name__ == "__main__":
    unittest.main()
