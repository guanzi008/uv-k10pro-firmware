# SPDX-License-Identifier: MIT
"""Independent synthetic records; no vendor dump or firmware in the suite."""

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
import channel_codec as cc


def record_pattern():
    return bytes((i * 7 + 11) & 255 for i in range(64))


class ChannelCodecTests(unittest.TestCase):
    def test_normal_vfo_attribute_and_scan_addresses(self):
        self.assertEqual(cc.channel_offset(0), 0)
        self.assertEqual(cc.channel_offset(998), 0xF980)
        self.assertEqual(cc.vfo_offset(0, 0), 0x10000)
        self.assertEqual(cc.vfo_offset(0, 1), 0x10040)
        self.assertEqual(cc.vfo_offset(6, 1), 0x10340)
        self.assertEqual(cc.attribute_offset(998), 0x113E6)
        self.assertEqual(cc.attribute_offset(1005), 0x113ED)
        self.assertEqual(cc.scan_list_offset(31), 0x16F80)
        for value in (-1, 999, True):
            with self.subTest(value=value), self.assertRaises(cc.ChannelError):
                cc.channel_offset(value)
        with self.assertRaises(cc.ChannelError):
            cc.vfo_offset(7, 0)
        with self.assertRaises(cc.ChannelError):
            cc.vfo_offset(0, 2)
        with self.assertRaises(cc.ChannelError):
            cc.scan_list_offset(32)

    def test_raw_roundtrip_never_normalizes_invalid_fields(self):
        for raw in (record_pattern(), b"\xff" * 64, bytes(64)):
            record = cc.ChannelRecord(raw)
            self.assertEqual(record.with_updates().raw, raw)
            self.assertEqual(bytes.fromhex(record.describe()["raw_hex"]), raw)
        for size in (0, 63, 65):
            with self.assertRaises(cc.ChannelError):
                cc.ChannelRecord(bytes(size))

    def test_frequency_edits_use_little_endian_ten_hz_and_preserve_other_bytes(self):
        before = record_pattern()
        updated = cc.ChannelRecord(before).with_updates(rx_frequency_hz=145_525_000, offset_hz=600_000)
        self.assertEqual(updated.raw[:8], struct.pack("<II", 14_552_500, 60_000))
        self.assertEqual(updated.raw[8:], before[8:])
        self.assertEqual(updated.describe()["rx_frequency_hz"], 145_525_000)
        for hz in (-10, 145_525_001, 2**32 * 10, 1.0, True):
            with self.subTest(hz=hz), self.assertRaises(cc.ChannelError):
                cc.frequency_units(hz)
        with self.assertRaises(cc.ChannelError):
            updated.with_updates(offset_hz=1_000_000_000)

    def test_name_edits_preserve_six_uninterpreted_allocation_bytes(self):
        before = record_pattern()
        result = cc.ChannelRecord(before).with_updates(name="TEST")
        self.assertEqual(result.raw[24:34], b"TEST\0\0\0\0\0\0")
        self.assertEqual(result.raw[:24], before[:24])
        self.assertEqual(result.raw[34:], before[34:])
        chinese = cc.ChannelRecord(before).with_updates(name="测试")
        self.assertEqual(chinese.describe()["name"], "测试")
        for bad in ("12345678901", "测试信道名称", "a\0b", b"a\xff", "😀"):
            with self.subTest(bad=bad), self.assertRaises(cc.ChannelError):
                cc.ChannelRecord(before).with_updates(name=bad)

    def test_tone_units_octal_dcs_and_unknown_values(self):
        before = record_pattern()
        result = cc.ChannelRecord(before).with_updates(rx_tone=cc.Tone(1, 885), tx_tone=cc.Tone(3, 0o23))
        self.assertEqual(result.raw[10:12], b"\1\3")
        self.assertEqual(result.raw[40:48], struct.pack("<II", 885, 19))
        self.assertEqual(result.rx_tone.describe()["frequency_hz"], "88.5")
        self.assertEqual(result.tx_tone.describe()["display"], "D023I")
        changed = {10, 11, 40, 41, 42, 43, 44, 45, 46, 47}
        self.assertTrue(all(old == new for i, (old, new) in enumerate(zip(before, result.raw)) if i not in changed))
        for tone in (cc.Tone(0, 1), cc.Tone(1, 599), cc.Tone(1, 2601),
                     cc.Tone(2, 512), cc.Tone(4, 0x800000), cc.Tone(5, 0x1000000)):
            with self.subTest(tone=tone):
                self.assertEqual(tone.sanitized(), cc.Tone(0, 0))
                with self.assertRaises(cc.ChannelError):
                    result.with_updates(rx_tone=tone)
        for tone in (cc.Tone(1, 600), cc.Tone(1, 2600), cc.Tone(2, 511),
                     cc.Tone(4, 0x7FFFFF), cc.Tone(5, 0xFFFFFF)):
            self.assertEqual(tone.sanitized(), tone)

    def test_confirmed_enums_and_step_preserve_unknown_regions(self):
        before = record_pattern()
        result = cc.ChannelRecord(before).with_updates(power=2, bandwidth=1, modulation=3,
                                                       reverse=1, scan_list=32, step_index=6)
        report = result.describe()
        self.assertEqual(report["power_name"], "TURBO")
        self.assertEqual(report["bandwidth_name"], "NARROW")
        self.assertEqual(report["modulation_name_8_02_16"], "CW")
        self.assertEqual(report["step_hz"], 8330)
        self.assertEqual(result.raw[34:40], before[34:40])
        self.assertEqual(result.raw[56:60], before[56:60])
        self.assertEqual(result.raw[62:64], before[62:64])
        for changes in ({"scan_list": 33}, {"power": 3}, {"reverse": True},
                        {"bandwidth": 2}, {"guessed_flag": 1}):
            with self.subTest(changes=changes), self.assertRaises(cc.ChannelError):
                result.with_updates(**changes)

    def test_offset_arithmetic_including_signed_clamp_and_wrap(self):
        self.assertEqual(cc.derived_tx_units(14_552_500, 60_000, 0), 14_552_500)
        self.assertEqual(cc.derived_tx_units(14_552_500, 60_000, 1), 14_612_500)
        self.assertEqual(cc.derived_tx_units(14_552_500, 60_000, 2), 14_492_500)
        self.assertEqual(cc.derived_tx_units(2_500_000, 3_000_000, 2), 2_500_000)
        self.assertEqual(cc.derived_tx_units(58_000_000, 1, 1), 58_000_000)
        self.assertEqual(cc.derived_tx_units(0xFFFFFFFF, 3_000_001, 1), 3_000_000)
        self.assertEqual(cc.derived_tx_units(14_552_500, 0, 255), 2_500_000)

    def test_dump_patch_retains_unknown_metadata_and_other_channels(self):
        image = bytes((i * 11 + 23) & 255 for i in range(0x12000))
        record = cc.read_channel(image, 77).with_updates(name="LOCAL", power=1)
        updated = cc.replace_channel(image, 77, record)
        self.assertEqual(updated[:4928], image[:4928])
        self.assertEqual(updated[4992:], image[4992:])
        self.assertEqual(cc.read_attribute(updated, 77), image[0x1104D])
        self.assertEqual(cc.read_channel(updated, 77), record)
        updated = cc.replace_channel(updated, 77, record, attribute=0xA2)
        self.assertEqual(updated[0x1104D], 0xA2)
        self.assertEqual(cc.attribute_description(0xA2)["band_index"], 2)
        self.assertEqual(cc.attribute_description(0xA2)["upper_nibble"], 10)

    def test_logical_delete_changes_one_attribute_and_retains_record(self):
        image = b"\x55" * 0x12000
        result = cc.delete_channel(image, 998)
        self.assertEqual(result[:0x113E6], image[:0x113E6])
        self.assertEqual(result[0x113E6], 255)
        self.assertEqual(result[0x113E7:], image[0x113E7:])
        self.assertEqual(cc.read_channel(result, 998).raw, b"\x55" * 64)
        self.assertEqual(cc.changed_regions(image, result),
                         [{"offset": 0x113E6, "length": 1, "before_hex": "55", "after_hex": "ff"}])

    def test_short_dump_rejected_without_silent_extension(self):
        with self.assertRaises(cc.ChannelError):
            cc.read_channel(bytes(63), 0)
        with self.assertRaises(cc.ChannelError):
            cc.read_attribute(bytes(0x11000), 0)
        with self.assertRaises(cc.ChannelError):
            cc.replace_channel(bytes(63), 0, cc.ChannelRecord(bytes(64)))
        with self.assertRaises(cc.ChannelError):
            cc.replace_channel(bytes(64), 0, cc.ChannelRecord(bytes(64)), attribute=2)
        with self.assertRaises(cc.ChannelError):
            cc.delete_channel(bytes(64), 0)

    def test_scan_list_member_layout_preserves_priorities_tail_and_duplicates(self):
        raw = bytes((i * 13 + 3) & 255 for i in range(128))
        record = cc.ScanListRecord(raw).with_members([0, 77, 998, 77])
        self.assertEqual(record.member_slots[:4], (0, 77, 998, 77))
        self.assertEqual(record.member_slots[4:], (65535,) * 44)
        self.assertEqual(record.raw[:24], raw[:24])
        self.assertEqual(record.raw[120:], raw[120:])
        self.assertEqual(record.priorities, struct.unpack_from("<2H", raw, 16))
        for members in ([999], [-1], [0] * 49):
            with self.subTest(members=members), self.assertRaises(cc.ChannelError):
                record.with_members(members)

    def test_cli_patch_reports_local_changes_and_requires_explicit_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.bin"
            output = Path(directory) / "edited.bin"
            source.write_bytes(b"\xff" * 0x12000)
            command = [sys.executable, str(TOOLS / "channel_codec.py"), "patch", str(source),
                       str(output), "--index", "77", "--name", "TEST", "--rx-hz", "145525000",
                       "--set", "power=1", "--rx-tone", "2:0o23"]
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            report = json.loads(result.stdout)
            self.assertEqual(report["operation"], "offline dump edit")
            record = cc.read_channel(output.read_bytes(), 77)
            self.assertEqual(record.describe()["name"], "TEST")
            self.assertEqual(record.rx_tone, cc.Tone(2, 19))
            self.assertEqual(source.read_bytes(), b"\xff" * 0x12000)
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            subprocess.run(command + ["--force"], check=True, capture_output=True)

    @unittest.skipUnless(os.environ.get("K10_REFERENCE_RAW"), "optional stock raw image not supplied")
    def test_optional_actual_stock_instruction_replay(self):
        # Imports Unicorn only when the explicit, local reference gate is used.
        import emulate_channels
        report = emulate_channels.experiments(Path(os.environ["K10_REFERENCE_RAW"]).read_bytes())
        self.assertTrue(report["checks_passed"])


if __name__ == "__main__":
    unittest.main()
