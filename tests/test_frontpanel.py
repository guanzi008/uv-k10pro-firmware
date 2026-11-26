# SPDX-License-Identifier: MIT
"""Check renderer boundaries and unavailable/stale diagnostic states."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FrontpanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shlex.split(os.environ.get("HOST_CC", "cc"))
        if not compiler or shutil.which(compiler[0]) is None:
            raise unittest.SkipTest("Host C compiler unavailable")
        cls.directory = tempfile.TemporaryDirectory(prefix="k10-frontpanel-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.executable = Path(cls.directory.name) / "frontpanel"
        command = compiler + [
            "-std=c11", "-O1", "-Wall", "-Wextra", "-Werror", "-Wshadow",
            "-Wundef", "-Wstrict-prototypes", "-fsanitize=undefined",
            "-fno-sanitize-recover=all", "-DK10_ENABLE_KEYPAD=0",
            "-I", str(ROOT / "firmware/include"),
            str(ROOT / "firmware/src/frontpanel.c"),
            str(ROOT / "firmware/src/keypad.c"),
            str(ROOT / "tests/test_frontpanel.c"), "-o", str(cls.executable),
        ]
        built = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if built.returncode:
            raise RuntimeError(built.stdout + built.stderr)

    def case(self, name):
        result = subprocess.run([str(self.executable), name],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"PASS: frontpanel {name}", result.stdout)

    def test_buffer_bounds_null_and_maximum_fields(self):
        self.case("bounds")

    def test_unavailable_stale_unknown_and_zero_are_distinct(self):
        self.case("states")


if __name__ == "__main__":
    unittest.main()
