# SPDX-License-Identifier: MIT
"""Synthetic layout checks: no vendor schema, firmware, or device is required."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import inspect_vendor_schema as inspector  # noqa: E402


def synthetic():
    return {
        "deviceModel": "TEST", "addOffset": "0x1000", "enums": {},
        "table": {
            "rows": {
                "type": "list", "addr": "0x200", "byteSize": 32, "maxCount": 3,
                "fields": [
                    {"key": "ui_index", "type": "empty"},
                    {"key": "frequency", "type": "int", "offset": 0, "length": 4,
                     "name": "Frequency (MHz)", "valueReadFormat": "unknownFrequency"},
                    {"key": "members", "type": "list", "offset": 8, "length": 12,
                     "byteSize": 2, "maxCount": 6},
                ],
            },
            "filter": {
                "type": "object", "addr": "0x407", "byteSize": 1, "bitSize": 8,
                "fields": [
                    {"key": "high", "type": "bool", "offset": 7, "length": 1},
                    {"key": "low", "type": "bool", "offset": 0, "length": 1},
                ],
            },
            "general": {
                "type": "group", "addr": 0,
                "fields": [{"key": "delay", "addr": "0x500", "offset": 6,
                            "length": 2, "type": "int", "name": "Delay [ms]"}],
            },
        },
    }


class LayoutTests(unittest.TestCase):
    def test_bit_offsets_share_one_byte_and_do_not_overlap(self):
        output = inspector.summarize(synthetic())
        fields = output["tables"]["filter"]["fields"]
        self.assertEqual([x["address"] for x in fields], [0x1407, 0x1407])
        self.assertEqual([x["bit_in_byte"] for x in fields], [7, 0])
        self.assertEqual([x["size_bits"] for x in fields], [1, 1])
        self.assertEqual([x["length_unit"] for x in fields], ["bit", "bit"])
        self.assertEqual(output["warnings"], [])

    def test_list_geometry_and_nested_list_are_distinct(self):
        row = inspector.summarize(synthetic())["tables"]["rows"]
        self.assertEqual(row["record_stride_bytes"], 32)
        self.assertEqual(row["records_end_exclusive"], 0x1260)
        member = row["fields"][2]
        self.assertEqual(member["address"], 0x1208)
        self.assertEqual(member["size_bits"], 96)
        self.assertEqual(member["nested_array"], {"stride_bytes": 2, "max_count": 6})
        self.assertEqual(row["fields"][0]["storage"], "ui-only")

    def test_field_override_is_not_added_to_table_base_twice(self):
        schema = synthetic()
        schema["table"]["general"]["addr"] = "0x999"
        field = inspector.summarize(schema)["tables"]["general"]["fields"][0]
        self.assertEqual(field["address"], 0x1506)

    def test_display_units_never_imply_stored_scaling(self):
        summary = inspector.summarize(synthetic())
        frequency = summary["tables"]["rows"]["fields"][1]
        delay = summary["tables"]["general"]["fields"][0]
        self.assertEqual(frequency["declared_display_unit"], "MHz")
        self.assertEqual(delay["declared_display_unit"], "ms")
        self.assertEqual(frequency["value_storage_unit"], "unresolved")
        self.assertEqual(delay["value_storage_unit"], "unresolved")

    def test_overlapping_group_fields_are_reported_not_silently_fixed(self):
        schema = synthetic()
        schema["table"]["alias"] = {
            "type": "group", "addr": 0,
            "fields": [{"key": "flag", "addr": "0x500", "offset": 7,
                        "length": 1, "type": "bool"}],
        }
        overlaps = inspector.summarize(schema)["warnings"]
        self.assertEqual(len(overlaps), 1)
        self.assertEqual(overlaps[0]["size_bits"], 8)
        self.assertEqual(overlaps[0]["fields"], ["general.delay", "alias.flag"])

    def test_record_and_bit_limits_produce_separate_warnings(self):
        schema = synthetic()
        schema["table"]["rows"]["fields"][1]["offset"] = 31
        schema["table"]["filter"]["fields"][0]["offset"] = 8
        bad = [x for x in inspector.summarize(schema)["warnings"]
               if x["kind"] == "field-outside-record"]
        self.assertEqual({x["unit"] for x in bad}, {"byte", "bit"})

    def test_group_without_bit_size_keeps_byte_offsets(self):
        schema = synthetic()
        schema["table"]["filter"].pop("bitSize")
        field = inspector.summarize(schema)["tables"]["filter"]["fields"][0]
        self.assertEqual(field["address"], 0x140E)
        self.assertEqual(field["offset_unit"], "byte")

    def test_auxiliary_flag_is_separate_from_record_data(self):
        schema = synthetic()
        schema["table"]["rows"]["flag"] = {"addr": "0x700", "offset": 2, "length": 3}
        row = inspector.summarize(schema)["tables"]["rows"]
        self.assertEqual(row["auxiliary_flag"]["address"], 0x1702)
        self.assertEqual(row["auxiliary_flag"]["length_bytes"], 3)
        self.assertEqual(row["records_end_exclusive"], 0x1260)

    def test_boolean_negative_and_fractional_offsets_are_rejected(self):
        for value in (True, -1, 1.5, "invalid"):
            with self.subTest(value=value):
                schema = synthetic()
                schema["table"]["rows"]["fields"][1]["offset"] = value
                with self.assertRaises(inspector.SchemaError):
                    inspector.summarize(schema)

    def test_duplicate_field_keys_are_rejected(self):
        schema = synthetic()
        schema["table"]["rows"]["fields"].append(deepcopy(schema["table"]["rows"]["fields"][1]))
        with self.assertRaisesRegex(inspector.SchemaError, "duplicate field"):
            inspector.summarize(schema)


class ComparisonTests(unittest.TestCase):
    def test_field_type_change_is_reported_even_when_address_is_unchanged(self):
        before = synthetic()
        after = deepcopy(before)
        after["table"]["filter"]["fields"][0]["type"] = "int"
        changes = inspector.compare_summaries(inspector.summarize(before), inspector.summarize(after))
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["path"], "/tables/filter/fields/high/logical_type")

    def test_field_reordering_is_not_a_layout_change(self):
        before = synthetic()
        after = deepcopy(before)
        after["table"]["filter"]["fields"].reverse()
        self.assertEqual(inspector.compare_summaries(inspector.summarize(before), inspector.summarize(after)), [])

    def test_enum_labels_change_hash_without_republishing_labels(self):
        before = synthetic()
        before["enums"]["mode"] = [{"name": "first"}]
        after = deepcopy(before)
        after["enums"]["mode"] = [{"name": "second"}]
        changes = inspector.compare_summaries(inspector.summarize(before), inspector.summarize(after))
        self.assertEqual([x["path"] for x in changes], ["/enums/mode/declaration_sha256"])
        self.assertNotIn("second", json.dumps(changes))


class LoadingTests(unittest.TestCase):
    def test_apk_and_json_produce_same_layout_without_extracting_members(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = json.dumps(synthetic()).encode()
            plain = root / "schema.json"
            apk = root / "test.apk"
            plain.write_bytes(payload)
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr(inspector.DEFAULT_ASSET, payload)
                archive.writestr("../untrusted.txt", "do not extract")
            a, am = inspector.load_schema(plain)
            b, bm = inspector.load_schema(apk)
            self.assertEqual(a, b)
            self.assertEqual(am["schema_sha256"], bm["schema_sha256"])
            self.assertEqual(sorted(x.name for x in root.iterdir()), ["schema.json", "test.apk"])

    def test_missing_or_duplicate_asset_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            apk = Path(temp) / "test.apk"
            for count in (0, 2):
                with self.subTest(count=count):
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", UserWarning)
                        with zipfile.ZipFile(apk, "w") as archive:
                            archive.writestr("irrelevant", "x")
                            for _ in range(count):
                                archive.writestr(inspector.DEFAULT_ASSET, "{}")
                    with self.assertRaises(inspector.SchemaError):
                        inspector.load_schema(apk)

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "schema.json"
            path.write_text('{"table": {}, "table": {}}')
            with self.assertRaisesRegex(inspector.SchemaError, "duplicate JSON key"):
                inspector.load_schema(path)

    def test_large_compressed_schema_is_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.apk"
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(inspector.DEFAULT_ASSET, b" " * (inspector.MAX_SCHEMA_BYTES + 1))
            with self.assertRaisesRegex(inspector.SchemaError, "size limit"):
                inspector.load_schema(path)


if __name__ == "__main__":
    unittest.main()
