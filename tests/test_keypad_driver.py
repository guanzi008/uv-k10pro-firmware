# SPDX-License-Identifier: MIT
"""Compile the actual keypad driver against a GPIO contact model."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class KeypadDriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shlex.split(os.environ.get("HOST_CC", "cc"))
        if not compiler or shutil.which(compiler[0]) is None:
            raise unittest.SkipTest("Host C compiler unavailable")
        cls.directory = tempfile.TemporaryDirectory(prefix="k10-keypad-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.executables = {}
        for enabled in (0, 1):
            target = Path(cls.directory.name) / f"keypad-{enabled}"
            command = compiler + [
                "-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                "-Wshadow", "-Wundef", "-Wstrict-prototypes",
                "-fsanitize=undefined", "-fno-sanitize-recover=all",
                f"-DK10_ENABLE_KEYPAD={enabled}",
                "-I", str(ROOT / "tests/mocks"),
                "-I", str(ROOT / "firmware/include"),
                str(ROOT / "tests/test_keypad_driver.c"),
                str(ROOT / "firmware/src/keypad.c"), "-o", str(target),
            ]
            built = subprocess.run(command, capture_output=True, text=True, timeout=30)
            if built.returncode:
                raise RuntimeError(built.stdout + built.stderr)
            cls.executables[enabled] = target

    def case(self, name, enabled=1):
        result = subprocess.run([str(self.executables[enabled]), name],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"PASS: keypad {name}", result.stdout)

    def test_every_contact_direct_priority_and_ptt(self):
        self.case("geometry")

    def test_bounce_key_transition_ptt_and_timer_wrap(self):
        self.case("debounce")

    def test_gpio_ownership_and_rows_restored(self):
        self.case("preservation")

    def test_invalid_call_does_not_touch_hardware_or_output(self):
        self.case("invalid")

    def test_default_disabled_never_touches_registers(self):
        self.case("disabled", enabled=0)


if __name__ == "__main__":
    unittest.main()
