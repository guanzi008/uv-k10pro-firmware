#!/usr/bin/env python3
"""Check fixed UV-K10 Pro 8.02.16 static-evidence anchors. No vendor binary bundled.

The checker verifies provenance and exact instruction/data bytes only. It does not
execute firmware, emulate peripherals, decode live radio, or validate PCB wiring.
Example:
  python verify_ble_audio_static.py --sample /path/to/analysis_only.raw.bin --output result.json
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", required=True, type=Path)
    parser.add_argument("--manifest", type=Path,
                        default=Path(__file__).resolve().parents[1] / "docs" / "evidence" / "ble-audio-static-anchors.json")
    parser.add_argument("--output", type=Path,
                        help="Optional JSON output path; otherwise print result to stdout")
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        raw = args.sample.read_bytes()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    sample = manifest["sample"]
    digest = hashlib.sha256(raw).hexdigest()
    base = int(sample["base_address"], 16)
    sample_matches = digest == sample["sha256"] and len(raw) == sample["size"]
    results = []
    for anchor in manifest["anchors"]:
        offset = int(anchor["address"], 16) - base
        expected = bytes.fromhex(anchor["expected_hex"])
        size = anchor["size"]
        if size != len(expected) or offset < 0:
            parser.error("Invalid anchor in manifest: " + anchor["id"])
        actual = raw[offset:offset + size]
        results.append({"id": anchor["id"], "address": anchor["address"],
                        "size": size, "matched": actual == expected,
                        "actual_hex": actual.hex(" "),
                        "interpretation": anchor["interpretation"]})
    success = sample_matches and all(item["matched"] for item in results)
    output = {"audit_type": "offline_static_byte_validation",
              "success": success, "sample_matches": sample_matches,
              "sample_sha256": digest, "sample_size": len(raw),
              "anchor_count": len(results),
              "matched_anchor_count": sum(item["matched"] for item in results),
              "hardware_or_emulation_run": False,
              "results": results,
              "conditional_calculations": manifest["conditional_calculations"],
              "confidence_and_limits": manifest["confidence"]}
    rendered = json.dumps(output, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
        print(json.dumps({"success": success, "anchors": len(results),
                          "matched": output["matched_anchor_count"],
                          "output": str(args.output)}, ensure_ascii=False))
    else:
        print(rendered, end="")
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
