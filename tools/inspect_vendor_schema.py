#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Summarize a locally supplied vendor JSON schema, without executing the app.

Only declarative layout facts and formatter names are retained. This tool does
not implement the vendor's formatters, parse radio memory, or write a radio.
APK content is read in memory; no archive member is extracted to the filesystem.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
import zipfile

DEFAULT_ASSET = "assets/flutter_assets/assets/K8.json"
MAX_SCHEMA_BYTES = 2 * 1024 * 1024


class SchemaError(ValueError):
    """The input cannot be represented as an unambiguous schema summary."""


def integer(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise SchemaError(f"{label}: boolean is not an address or size")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str):
        try:
            result = int(value, 16 if value.lower().startswith("0x") else 10)
        except ValueError as exc:
            raise SchemaError(f"{label}: invalid integer {value!r}") from exc
    else:
        raise SchemaError(f"{label}: expected integer, got {type(value).__name__}")
    if result < 0:
        raise SchemaError(f"{label}: negative addresses or sizes are unsupported")
    return result


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SchemaError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_schema(path: Path | str, asset: str = DEFAULT_ASSET) -> tuple[dict, dict]:
    """Read JSON or one exact APK member; reject duplicates and oversized JSON."""
    path = Path(path)
    metadata: dict[str, Any] = {"filename": path.name, "container_bytes": path.stat().st_size}
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            matches = [item for item in archive.infolist() if item.filename == asset]
            if len(matches) != 1:
                raise SchemaError(f"expected exactly one APK asset {asset!r}; got {len(matches)}")
            if matches[0].file_size > MAX_SCHEMA_BYTES:
                raise SchemaError("schema asset exceeds size limit")
            with archive.open(matches[0]) as stream:
                data = stream.read(MAX_SCHEMA_BYTES + 1)
        metadata.update(container_type="zip/apk", asset=asset,
                        container_sha256=_digest_file(path))
    else:
        with path.open("rb") as stream:
            data = stream.read(MAX_SCHEMA_BYTES + 1)
        metadata["container_type"] = "json"
    if len(data) > MAX_SCHEMA_BYTES:
        raise SchemaError("schema exceeds size limit")
    try:
        parsed = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_object_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaError(f"invalid UTF-8 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SchemaError("schema root must be an object")
    metadata.update(schema_bytes=len(data), schema_sha256=hashlib.sha256(data).hexdigest())
    return parsed, metadata


def _display_unit(field: dict) -> str | None:
    """Read explicit units in a UI label, never infer the stored scaling."""
    name = str(field.get("name", ""))
    for expression, unit in (
        (r"[\[(（［]\s*mhz\s*[\])）］]", "MHz"),
        (r"[\[(（［]\s*khz\s*[\])）］]", "kHz"),
        (r"[\[(（［]\s*ms\s*[\])）］]", "ms"),
        (r"[\[(（［]\s*(?:s|秒)\s*[\])）］]", "s"),
        (r"[\[(（［]\s*(?:min|分钟)\s*[\])）］]", "min"),
    ):
        if re.search(expression, name, flags=re.I):
            return unit
    return None


def _ranges(fields: list[dict]) -> list[dict]:
    intervals = sorted((f["address"], (f["absolute_bit_offset"] + f["size_bits"] + 7) // 8)
                       for f in fields if f["storage"] == "mapped")
    merged: list[list[int]] = []
    for begin, end in intervals:
        if merged and begin <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([begin, end])
    return [{"start": start, "end_exclusive": end} for start, end in merged]


def _overlaps(fields: list[dict], scope: str) -> list[dict]:
    mapped = [f for f in fields if f["storage"] == "mapped" and f["size_bits"]]
    result = []
    for index, a in enumerate(mapped):
        for b in mapped[index + 1:]:
            start = max(a["absolute_bit_offset"], b["absolute_bit_offset"])
            end = min(a["absolute_bit_offset"] + a["size_bits"],
                      b["absolute_bit_offset"] + b["size_bits"])
            if start < end:
                result.append({"kind": "declared-overlap", "scope": scope,
                               "fields": [a["path"], b["path"]],
                               "start_bit": start, "size_bits": end - start,
                               "interpretation": "Possible alias or schema error; not firmware proof."})
    return result


def summarize(schema: dict, source: dict | None = None) -> dict:
    tables = schema.get("table")
    if not isinstance(tables, dict):
        raise SchemaError("schema.table must be an object")
    add_offset = integer(schema.get("addOffset", 0), "addOffset")
    enums = schema.get("enums", {})
    if not isinstance(enums, dict):
        raise SchemaError("schema.enums must be an object")
    enum_summary = {}
    for key, values in enums.items():
        encoded = json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        enum_summary[key] = {"count": len(values) if isinstance(values, list) else None,
                             "declaration_sha256": hashlib.sha256(encoded).hexdigest()}
    output: dict[str, Any] = {
        "summary_version": 1, "evidence_level": "vendor-app-declaration-only",
        "device_model": schema.get("deviceModel"), "add_offset": add_offset,
        "source": source or {}, "tables": {}, "enums": enum_summary, "warnings": [],
        "limitations": [
            "Addresses describe app declarations; firmware behavior requires independent validation.",
            "Object bitSize makes child offsets and lengths bit units, not byte units.",
            "Formatter names are identifiers only; byte order and stored-value scaling remain unresolved.",
            "Record-field addresses are for record zero; no radio data is read or written.",
        ],
    }
    scalar_fields = []
    for table_id, table in tables.items():
        if not isinstance(table, dict):
            raise SchemaError(f"table.{table_id}: expected object")
        kind = table.get("type", "unknown")
        base = integer(table.get("addr", 0), f"{table_id}.addr") + add_offset
        stride = integer(table["byteSize"], f"{table_id}.byteSize") if "byteSize" in table else None
        count = integer(table["maxCount"], f"{table_id}.maxCount") if "maxCount" in table else None
        bits = integer(table["bitSize"], f"{table_id}.bitSize") if "bitSize" in table else None
        bit_fields = kind == "object" and bits is not None
        fields = table.get("fields", [])
        if not isinstance(fields, list):
            raise SchemaError(f"{table_id}.fields: expected array")
        table_out: dict[str, Any] = {
            "kind": kind, "declared_key": table.get("key"), "base_address": base,
            "record_stride_bytes": stride if kind == "list" else None,
            "declared_byte_size": stride, "max_count": count, "declared_bit_size": bits,
            "offset_unit": "bit" if bit_fields else "byte", "fields": [],
        }
        if kind == "list" and stride is not None and count is not None:
            table_out["records_end_exclusive"] = base + stride * count
        for converter in ("valueReadFormat", "valueWriteFormat"):
            if converter in table:
                table_out[converter] = table[converter]
        keys: set[str] = set()
        for index, field in enumerate(fields):
            if not isinstance(field, dict):
                raise SchemaError(f"{table_id}.fields[{index}]: expected object")
            key = str(field.get("key", f"unnamed_{index}"))
            if key in keys:
                raise SchemaError(f"{table_id}: duplicate field key {key!r}")
            keys.add(key)
            item: dict[str, Any] = {"key": key, "path": f"{table_id}.{key}",
                                    "logical_type": field.get("type"),
                                    "input_type": field.get("inputType"),
                                    "storage": "unmapped",
                                    "value_storage_unit": "unresolved",
                                    "declared_display_unit": _display_unit(field)}
            for name in ("valueReadFormat", "valueWriteFormat", "optionKey"):
                if name in field:
                    item[name] = field[name]
            if "optionKey" in field:
                item["enum_count"] = enum_summary.get(field["optionKey"], {}).get("count")
            if "byteSize" in field or "maxCount" in field:
                item["nested_array"] = {
                    "stride_bytes": integer(field["byteSize"], item["path"] + ".byteSize") if "byteSize" in field else None,
                    "max_count": integer(field["maxCount"], item["path"] + ".maxCount") if "maxCount" in field else None,
                }
            if field.get("type") == "empty" or field.get("isStatic"):
                item["storage"] = "ui-only"
            elif "offset" in field and "length" in field:
                offset = integer(field["offset"], item["path"] + ".offset")
                length = integer(field["length"], item["path"] + ".length")
                field_base = integer(field["addr"], item["path"] + ".addr") + add_offset if "addr" in field else base
                factor = 1 if bit_fields else 8
                absolute = field_base * 8 + offset * factor
                item.update(storage="mapped", offset=offset, length=length,
                            offset_unit=table_out["offset_unit"], length_unit=table_out["offset_unit"],
                            address_base=field_base, address=absolute // 8,
                            bit_in_byte=absolute % 8, absolute_bit_offset=absolute,
                            size_bits=length * factor)
                limit = bits if bit_fields else stride if kind == "list" else None
                if limit is not None and "addr" not in field and offset + length > limit:
                    output["warnings"].append({"kind": "field-outside-record", "field": item["path"],
                                               "unit": table_out["offset_unit"], "end": offset + length,
                                               "declared_limit": limit})
            table_out["fields"].append(item)
        flag = table.get("flag")
        if isinstance(flag, dict):
            table_out["auxiliary_flag"] = {
                "address": integer(flag.get("addr", 0), table_id + ".flag.addr") + add_offset + integer(flag.get("offset", 0), table_id + ".flag.offset"),
                "length_bytes": integer(flag["length"], table_id + ".flag.length") if "length" in flag else None,
                "read_formatter": flag.get("flagRead"), "write_formatter": flag.get("flagWrite"),
            }
        table_out["record_zero_mapped_ranges"] = _ranges(table_out["fields"])
        if kind == "list":
            output["warnings"].extend(_overlaps(table_out["fields"], f"{table_id}:record-zero"))
        else:
            scalar_fields.extend(table_out["fields"])
        output["tables"][table_id] = table_out
    output["warnings"].extend(_overlaps(scalar_fields, "non-list-groups"))
    return output


def compare_summaries(before: dict, after: dict) -> list[dict]:
    """Compare retained facts; ignore provenance hashes and descriptive warnings."""
    changes = []

    def walk(a: Any, b: Any, path: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(a.keys() | b.keys()):
                if key not in a:
                    changes.append({"path": path + "/" + key, "change": "added", "after": b[key]})
                elif key not in b:
                    changes.append({"path": path + "/" + key, "change": "removed", "before": a[key]})
                else:
                    walk(a[key], b[key], path + "/" + key)
        elif isinstance(a, list) and isinstance(b, list):
            # Fields have stable schema keys; comparing by them avoids reorder noise.
            if all(isinstance(x, dict) and "key" in x for x in a + b):
                walk({x["key"]: x for x in a}, {x["key"]: x for x in b}, path)
            elif a != b:
                changes.append({"path": path, "change": "modified", "before": a, "after": b})
        elif a != b:
            changes.append({"path": path, "change": "modified", "before": a, "after": b})

    for key in ("device_model", "add_offset", "tables", "enums"):
        walk(before[key], after[key], "/" + key)
    return changes


def markdown(summary: dict) -> str:
    lines = ["# Vendor app schema declarations", "",
             "These are app declarations, not verified firmware behavior. All field addresses refer to record zero.", "",
             "| Table | Base | Stride bytes | Max count | Offset unit |",
             "| --- | ---: | ---: | ---: | --- |"]
    for key, table in summary["tables"].items():
        lines.append(f"| {key} | 0x{table['base_address']:X} | {table['record_stride_bytes']} | {table['max_count']} | {table['offset_unit']} |")
    for key, table in summary["tables"].items():
        lines.extend(["", f"## {key}", "", "| Field | Address | Offset | Length | Unit | Type | Display unit |",
                      "| --- | ---: | ---: | ---: | --- | --- | --- |"])
        for field in table["fields"]:
            if field["storage"] != "mapped":
                continue
            lines.append(f"| {field['key']} | 0x{field['address']:X} | {field['offset']} | {field['length']} | {field['offset_unit']} | {field['logical_type']} | {field['declared_display_unit'] or 'unresolved'} |")
    lines.extend(["", "## Warnings", ""])
    lines.extend("- " + json.dumps(warning, ensure_ascii=False, sort_keys=True) for warning in summary["warnings"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="local vendor APK or extracted JSON")
    parser.add_argument("--asset", default=DEFAULT_ASSET, help="exact ZIP member, for APK input")
    parser.add_argument("--compare", type=Path, help="second APK/JSON; emit retained-layout differences")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", "-o", type=Path)
    args = parser.parse_args(argv)
    try:
        schema, source = load_schema(args.input, args.asset)
        summary = summarize(schema, source)
        if args.compare:
            if args.format != "json":
                raise SchemaError("comparison output requires --format json")
            second, second_source = load_schema(args.compare, args.asset)
            compared = summarize(second, second_source)
            result = {"evidence_level": "vendor-app-declaration-only", "before": source,
                      "after": second_source, "changes": compare_summaries(summary, compared)}
        else:
            result = summary
        text = markdown(result) if args.format == "markdown" else json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
    except (SchemaError, OSError, zipfile.BadZipFile) as exc:
        parser.exit(2, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
