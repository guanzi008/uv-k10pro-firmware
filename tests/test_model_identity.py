# SPDX-License-Identifier: MIT
"""Model-isolation regressions with independently created schema fragments."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import audit_model_identity as audit  # noqa: E402


def synthetic_k8():
    return {
        "deviceModel": "K8", "table": {
            "channel_list": {"addr": "0x0", "byteSize": 64, "maxCount": 999,
                             "flag": {"addr": "0x11000", "length": 999}},
            "vfo": {"addr": "0x10000", "byteSize": 64, "maxCount": 14},
        },
    }


class ModelIsolationTests(unittest.TestCase):
    def test_exact_schema_id_is_required(self):
        for model in ("K82", "K8-extra", "UV5", "k8", "UV-K10 Pro"):
            schema = synthetic_k8()
            schema["deviceModel"] = model
            with self.subTest(model=model), self.assertRaisesRegex(ValueError, "disagree"):
                audit.summarize_schema(schema, "K8")

    def test_similar_id_and_shared_helpers_do_not_override_geometry(self):
        schema = synthetic_k8()
        schema["deviceModel"] = "K82"
        schema["table"]["channel_list"].update(byteSize=16, maxCount=200)
        schema["table"]["channel_list"]["flag"].update(addr="0xd60", length=200,
                                                          flagRead="k8FlagRead")
        summary = audit.summarize_schema(schema, "K82")
        self.assertFalse(summary["k10_layout_gate"]["matches_reference_geometry"])

    def test_label_alone_does_not_accept_foreign_layout(self):
        schema = synthetic_k8()
        schema["table"]["channel_list"]["byteSize"] = 16
        self.assertFalse(audit.summarize_schema(schema, "K8")["k10_layout_gate"]["matches_reference_geometry"])

    def test_all_geometry_discriminators_are_required(self):
        for table, field, value in (("channel_list", "addr", 64),
                                    ("channel_list", "byteSize", 16),
                                    ("channel_list", "maxCount", 200),
                                    ("vfo", "addr", 0xC80),
                                    ("vfo", "byteSize", 16),
                                    ("vfo", "maxCount", 7)):
            schema = synthetic_k8()
            schema["table"][table][field] = value
            with self.subTest(table=table, field=field):
                self.assertFalse(audit.summarize_schema(schema, "K8")["k10_layout_gate"]["matches_reference_geometry"])

    def test_additive_and_flag_offsets_are_included(self):
        schema = synthetic_k8()
        schema["addOffset"] = "0x100"
        shifted = audit.summarize_schema(schema, "K8")
        self.assertEqual(shifted["tables"]["channel_list"]["flag_address"], 0x11100)
        self.assertFalse(shifted["k10_layout_gate"]["matches_reference_geometry"])
        schema.pop("addOffset")
        schema["table"]["channel_list"]["flag"]["offset"] = 1
        self.assertFalse(audit.summarize_schema(schema, "K8")["k10_layout_gate"]["matches_reference_geometry"])

    def test_compatible_layout_is_not_live_identification(self):
        result = audit.summarize_schema(synthetic_k8(), "K8")["k10_layout_gate"]
        self.assertTrue(result["matches_reference_geometry"])
        self.assertFalse(result["live_device_identity_verified"])

    def test_unknown_samples_fail_before_vendor_container_parsing(self):
        for function in (audit.audit_apk, audit.audit_cps):
            with self.subTest(function=function.__name__), self.assertRaisesRegex(ValueError, "SHA-256"):
                function(b"unrecognized sample labelled UV-K10 Pro")


if __name__ == "__main__":
    unittest.main()
