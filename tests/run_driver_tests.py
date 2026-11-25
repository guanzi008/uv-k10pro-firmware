#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compile the real C drivers against bus models and run bounded scenarios."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = (
    "nor-normal", "nor-unknown", "nor-absent", "nor-timeout", "nor-fault",
    "nor-transfer-timeout",
    "nor-busy", "si-normal", "si-absent", "si-stuck", "si-stretch-timeout",
    "si-cts-timeout", "si-command-error", "si-wrong-part", "si-tune-timeout",
    "si-tune-mismatch",
)


class DriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build = ROOT / "build"
        build.mkdir(exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="host-drivers-", dir=build)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.enabled = Path(cls.temporary.name) / "enabled"
        cls.disabled = Path(cls.temporary.name) / "disabled"
        compiler = os.environ.get("HOST_CC", "cc")
        common = [
            compiler, "-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
            "-Wshadow", "-Wstrict-prototypes", "-fsanitize=undefined",
            "-fno-sanitize-recover=all", "-Itests/mocks", "-Ifirmware/include",
            "tests/test_drivers.c", "firmware/src/spi_nor.c",
            "firmware/src/si4732.c",
        ]
        subprocess.run(common + ["-o", str(cls.enabled)], cwd=ROOT, check=True, timeout=30)
        subprocess.run(common + ["-DK10_ENABLE_SPI_NOR=0", "-DK10_ENABLE_SI4732=0",
                                 "-o", str(cls.disabled)], cwd=ROOT, check=True, timeout=30)

    def test_enabled_drivers(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                completed = subprocess.run(
                    [str(self.enabled), scenario], cwd=ROOT, timeout=5,
                    capture_output=True, text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_disabled_drivers_never_access_registers(self):
        completed = subprocess.run(
            [str(self.disabled), "disabled"], cwd=ROOT, timeout=5,
            capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
