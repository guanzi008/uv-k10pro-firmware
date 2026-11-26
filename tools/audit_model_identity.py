#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Audit model boundaries in separately obtained APK/CPS/firmware samples.

APK and CPS checks are static. They do not start Android, Windows, or a CLR.
The optional raw-image check executes only a pinned ARM reply builder above
one explicitly replaced transport function; it does not connect to a radio.
Only metadata, short field labels, and independent findings are emitted.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile


APK_SAMPLES = {
    "b12adceddfe49c7df04eab3b8b6dbc336d7a6eb32a0df62bc75b7e978559d048":
        "official-direct-download-1.1.0-64",
    "0395fa0332d025da73a39aad49b01eaedfe59e8296f17df56fa937ff9b83b326":
        "dealer-resource-pack-1.1.0-68",
}
CPS_SHA256 = "79c9b3429187f7fca13cc8971d0e5fd46184f078127f149f333a267b99815132"
ASSET_ROOT = "assets/flutter_assets/assets/"
TABLE_KEYS = ("channel_list", "vfo", "flag_list", "channel_name_list",
              "scanning_list", "scan_list", "dtmfLinkman")
AOT_TERMS = (
    "protocolIdForModelName", "modelNameForProtocolId", "normalizeModelId",
    "selectProtocolForDeviceModel", "preferredDeviceModel",
    "K8RadioProtocol", "K82RadioProtocol", "K8Session", "K82Session",
    "assets/K8.json", "assets/K82.json", "assets/UV5.json", "UV-K10 Pro",
)


def integer(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("expected a non-negative integer or integer string")
    result = int(value, 0) if isinstance(value, str) else value
    if result < 0:
        raise ValueError("negative layout value")
    return result


def summarize_schema(schema, expected_id):
    """An asset filename is not enough: require its own exact model ID."""
    if schema.get("deviceModel") != expected_id:
        raise ValueError("asset path and deviceModel disagree")
    base = integer(schema.get("addOffset", 0))
    result = {"device_model": expected_id, "tables": {}}
    for key in TABLE_KEYS:
        row = schema["table"].get(key)
        if row is None:
            continue
        table = {"address": base + integer(row["addr"]),
                 "stride_bytes": integer(row["byteSize"]),
                 "count": integer(row["maxCount"])}
        if "flag" in row:
            flag = row["flag"]
            table["flag_address"] = base + integer(flag["addr"]) + integer(flag.get("offset", 0))
            table["flag_length_bytes"] = integer(flag["length"])
        result["tables"][key] = table
    result["channel_demode_labels"] = [x["name"] for x in schema.get("enums", {}).get("demode_option", [])]
    result["k10_layout_gate"] = k10_layout_gate(result)
    return result


def k10_layout_gate(summary):
    """Compare independently established geometry, not similarly named IDs.

    Passing this gate is a layout cross-check, never a live device identity
    or complete firmware compatibility check. It cannot authorize writes.
    """
    checks = {
        "exact_K8_id": summary.get("device_model") == "K8",
        "channel_geometry": summary.get("tables", {}).get("channel_list") == {
            "address": 0, "stride_bytes": 64, "count": 999,
            "flag_address": 0x11000, "flag_length_bytes": 999,
        },
        "vfo_geometry": summary.get("tables", {}).get("vfo") == {
            "address": 0x10000, "stride_bytes": 64, "count": 14,
        },
    }
    return {"matches_reference_geometry": all(checks.values()), "checks": checks,
            "live_device_identity_verified": False}


def audit_apk(raw):
    digest = hashlib.sha256(raw).hexdigest()
    if digest not in APK_SAMPLES:
        raise ValueError("unsupported APK SHA-256; model names are not an allowlist")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        products_bytes = archive.read(ASSET_ROOT + "products/data.json")
        products = json.loads(products_bytes)
        schemas = {}
        for model in ("K8", "K82", "UV5"):
            asset = ASSET_ROOT + model + ".json"
            data = archive.read(asset)
            schemas[model] = {"asset": asset, "bytes": len(data),
                              "sha256": hashlib.sha256(data).hexdigest(),
                              **summarize_schema(json.loads(data), model)}
        lib = archive.read("lib/arm64-v8a/libapp.so")
        terms = {term: lib.find(term.encode("ascii")) for term in AOT_TERMS}
        return {
            "sample": APK_SAMPLES[digest], "apk_sha256": digest, "apk_bytes": len(raw),
            "product_catalog_names": [x["name"] for x in products],
            "product_catalog_sha256": hashlib.sha256(products_bytes).hexdigest(),
            "schemas": schemas,
            "aot": {"architecture": "arm64-v8a", "bytes": len(lib),
                    "sha256": hashlib.sha256(lib).hexdigest(),
                    "string_byte_offsets": terms,
                    "limitation": "String presence/offsets do not recover the Dart routing call graph."},
            "conclusion": "K8 agrees with the K10 reference geometry; K82 and UV5 are separate schemas.",
        }


def audit_cps(raw):
    digest = hashlib.sha256(raw).hexdigest()
    if digest != CPS_SHA256:
        raise ValueError("unsupported extracted CPS assembly SHA-256")
    import dnfile
    from dncil.cil.body import CilMethodBody
    from dncil.cil.body.reader import CilMethodBodyReaderBytes

    pe = dnfile.dnPE(data=raw)
    names = {i: str(row.TypeNamespace) + "." + str(row.TypeName)
             for i, row in enumerate(pe.net.mdtables.TypeDef.rows, 1)}
    for row in pe.net.mdtables.NestedClass.rows:
        names[row.NestedClass.row_index] = names[row.EnclosingClass.row_index] + "+" + str(row.NestedClass.row.TypeName)
    owners = {}
    for i, row in enumerate(pe.net.mdtables.TypeDef.rows, 1):
        for entries, table in ((row.MethodList, 6), (row.FieldList, 4)):
            for entry in entries:
                owners[table, entry.row_index] = names[i]

    def token_name(value):
        table, index = value >> 24, value & 0xFFFFFF
        if table not in (4, 6):
            return None
        row = pe.net.mdtables.tables[table].rows[index - 1]
        return owners[table, index] + "::" + str(row.Name)

    def constant(inst):
        name = inst.opcode.name
        if name in ("ldc.i4", "ldc.i4.s"):
            return inst.operand
        if name.startswith("ldc.i4."):
            return -1 if name.endswith("m1") else int(name.rsplit(".", 1)[1])
        return None

    wanted = {
        "K7.main::.ctor", "K7.main::main_Load", "K7.main::.cctor", "K7.main::Login",
        "K7.protocol_struct::Link", "K7.param_string::chn_demode_Items",
        "K7.param_string::GetRadioChipTypes", "K7.cps_convert::GetSafeItem",
        "K7.cps_convert::GetIndex", "K7.channel::channel_Load", "K7.main::CellContentClick",
    }
    # These overloads, identified by MethodDef token, convert the 64-byte channel
    # structure to and from the UI structure. Other convert_data overloads exist.
    chosen_overloads = {0x06000025, 0x06000028}
    selected_calls = {
        "K7.main::GetLang", "K7.main::GridViewChannelInit", "K7.protocol_struct::Link",
        "K7.param_string::chn_demode_Items", "K7.param_string::GetRadioChipTypes",
        "K7.cps_convert::GetSafeItem", "K7.cps_convert::GetIndex",
        "K7.main::LoadComboBoxString", "K7.channel::LoadComboBoxString",
    }
    methods, demode_callers = {}, []
    for index, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
        if not row.Rva:
            continue
        token = 0x06000000 + index
        name = token_name(token)
        instructions = CilMethodBody(CilMethodBodyReaderBytes(pe.get_data(row.Rva))).instructions
        calls, field_refs, strings, assignments = [], [], [], []
        for ins in instructions:
            value = getattr(ins.operand, "value", None)
            if ins.opcode.name in ("call", "callvirt") and value is not None:
                called = token_name(value)
                if called:
                    calls.append({"il_offset": hex(ins.offset), "target": called, "token": hex(value)})
                    if value == 0x060002E1:
                        demode_callers.append({"method": name, "token": hex(token),
                                               "rva": hex(row.Rva), "il_offset": hex(ins.offset)})
            if ins.opcode.name in ("ldfld", "ldsfld", "stfld", "stsfld") and value is not None:
                field_refs.append({"il_offset": hex(ins.offset), "operation": ins.opcode.name,
                                   "field": token_name(value)})
            if ins.opcode.name == "ldstr":
                strings.append(pe.net.user_strings.get(value & 0xFFFFFF).value)
        for previous, current in zip(instructions, instructions[1:]):
            value = constant(previous)
            if value is not None and current.opcode.name in ("stfld", "stsfld"):
                assignments.append({"field": token_name(current.operand.value), "value": value,
                                    "il_offset": hex(current.offset)})
        if name not in wanted and token not in chosen_overloads:
            continue
        # Restrict output to identity/label facts, not other UI strings or code.
        field_refs = [x for x in field_refs if x["field"] and x["field"].endswith(
            ("::ModelVersion", "::Model", "::u8ParamsVersion", "::demode", "::am", "::RadioModul"))]
        assignments = [x for x in assignments if x["field"] and x["field"].endswith("::ModelVersion")]
        labels = strings if name in ("K7.param_string::chn_demode_Items", "K7.param_string::GetRadioChipTypes") else [s for s in strings if s in ("Version 0.56", "model", "model_error")]
        methods[hex(token)] = {"name": name, "rva": hex(row.Rva), "selected_strings": labels,
                               "selected_field_refs": field_refs, "model_version_assignments": assignments,
                               "selected_internal_calls": [x for x in calls if x["target"] in selected_calls]}

    resource = next(r for r in pe.net.resources if r.name == "K7.Resource.resources")
    model = next(e.value for e in resource.data.entries if e.name == "model")
    if model != "UV-K10 Pro/UV-K10 Pro":
        raise ValueError("unexpected product resource")
    revisions = [x["value"] for x in methods["0x6000453"]["model_version_assignments"]]
    if revisions != [0]:
        raise ValueError("unexpected parameter-revision assignment")
    demode_labels = methods["0x60002e1"]["selected_strings"]
    if demode_labels != ["FM", "AM", "CW"]:
        raise ValueError("unexpected CPS demodulation labels")
    return {
        "assembly_sha256": digest, "assembly_bytes": len(raw),
        "namespace": "K7", "product_resource": model,
        "methods": methods, "demode_callers": demode_callers,
        "interpretation": {
            "product": "UV-K10 Pro", "parameter_revision_expected": revisions[0],
            "revision_source": "ConnRsp.u8ParamsVersion at body offset 22 -> protocol_struct.Model -> main.Login comparison",
            "revision_is_unique_product_id": False,
            "demode_labels": demode_labels,
            "demode_active_channel_read_write_path": True,
            "label_conflict_is_explained_by_wrong_CPS_model": False,
            "radio_chip_default": "Si4732", "radio_chip_default_proves_fitted_hardware": False,
        },
        "execution": "static PE/.NET metadata and IL parsing only; no vendor process or CLR",
    }


def audit_hello(raw):
    from emulate_stock import StockEmulator
    emu = StockEmulator(raw)
    init = emu.initialize_data()
    captures = []

    def capture(engine):
        length = engine.reg(2)
        if length != 56:
            raise ValueError("unexpected hello reply length")
        body = engine.read(engine.reg(1), length)
        captures.append({"transport": engine.reg(0), "body_bytes": length,
                         "command": int.from_bytes(body[:2], "little"),
                         "declared_inner_length": int.from_bytes(body[2:4], "little"),
                         "version": body[4:20].split(b"\0", 1)[0].decode("ascii"),
                         "parameter_revision_offset": 22, "parameter_revision": body[22]})
        return 0

    emu.hook_function(0x0800361C, capture, name="capture transport_send; no physical I/O")
    result = emu.call(0x080035B8, (1,))
    if len(captures) != 1 or captures[0]["parameter_revision"] != 0:
        raise ValueError("reference hello result changed")
    return {"raw_sha256": emu.sample_sha256, "function": "0x080035b8",
            "initialize_instructions": init.instructions, "function_instructions": result.instructions,
            "hardware_hooks": result.hardware_hooks, "transport_hook": "0x0800361c",
            "capture": captures[0], "mmio_modeled": False, "physical_io": False,
            "boundary": "Real reply-builder instructions; transport replaced; zero-initialized BSS plus original .data initialization."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, action="append", default=[])
    parser.add_argument("--cps", type=Path)
    parser.add_argument("--raw", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.apk and args.cps is None and args.raw is None:
        parser.error("supply at least one separately obtained sample")
    output = {"schema_version": 1, "kind": "model-identity-static-audit-and-optional-reply-replay"}
    if args.apk:
        output["android"] = [audit_apk(path.read_bytes()) for path in args.apk]
    if args.cps is not None:
        output["pc"] = audit_cps(args.cps.read_bytes())
    if args.raw is not None:
        output["firmware_hello"] = audit_hello(args.raw.read_bytes())
    encoded = json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
