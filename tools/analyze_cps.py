#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Statically cross-check the separately supplied official CPS 0.56 assembly.

Reads PE/.NET metadata and selected IL instructions using dnfile/dncil. Does
not load the assembly in a CLR, execute vendor instructions, or access devices.
The Setup Factory installer must first be extracted outside this repository.
"""

import argparse
import hashlib
import json
from pathlib import Path

REFERENCE_SHA256 = "79c9b3429187f7fca13cc8971d0e5fd46184f078127f149f333a267b99815132"


def analyze(raw):
    if not __debug__:
        raise ValueError("run without -O; the static cross-check requires its assertions")
    if hashlib.sha256(raw).hexdigest() != REFERENCE_SHA256:
        raise ValueError("unsupported extracted CPS assembly SHA-256")
    import dnfile
    from dncil.cil.body import CilMethodBody
    from dncil.cil.body.reader import CilMethodBodyReaderBytes

    pe = dnfile.dnPE(data=raw)
    names = {i: str(row.TypeNamespace) + "." + str(row.TypeName)
             for i, row in enumerate(pe.net.mdtables.TypeDef.rows, 1)}
    for row in pe.net.mdtables.NestedClass.rows:
        names[row.NestedClass.row_index] = names[row.EnclosingClass.row_index] + "+" + str(row.NestedClass.row.TypeName)
    method_owner, field_owner = {}, {}
    for i, row in enumerate(pe.net.mdtables.TypeDef.rows, 1):
        method_owner.update({entry.row_index: names[i] for entry in row.MethodList})
        field_owner.update({entry.row_index: names[i] for entry in row.FieldList})
    marshals = {row.Parent.row_index: bytes(row.NativeType.value) for row in pe.net.mdtables.FieldMarshal.rows}
    layouts = {row.Parent.row_index: row.PackingSize for row in pe.net.mdtables.ClassLayout.rows}

    def compressed_uint(data):
        first = data[0]
        if first < 0x80:
            return first
        if first < 0xC0:
            return ((first & 0x3F) << 8) | data[1]
        return ((first & 0x1F) << 24) | int.from_bytes(data[1:4], "big")

    scalar = {5: (1, "UInt8"), 7: (2, "UInt16"), 8: (4, "Int32"), 9: (4, "UInt32")}
    structures = {}

    def structure(index):
        if names[index] in structures:
            return structures[names[index]]
        assert layouts[index] == 1
        offset, fields = 0, []
        for entry in pe.net.mdtables.TypeDef.rows[index - 1].FieldList:
            field = entry.row
            if field.Flags.fdStatic:
                continue
            signature = bytes(field.Signature.value)
            assert signature[0] == 6
            kind = signature[1]
            if kind in scalar:
                size, type_name = scalar[kind]
            elif kind == 0x11:
                coded_type = compressed_uint(signature[2:])
                assert coded_type & 3 == 0
                nested = structure(coded_type >> 2)
                size, type_name = nested["size"], names[coded_type >> 2]
            elif kind == 0x1D:
                assert signature[2] in scalar and marshals[entry.row_index][0] == 0x1E
                count = compressed_uint(marshals[entry.row_index][1:])
                element_size, element_type = scalar[signature[2]]
                size, type_name = count * element_size, f"{element_type}[{count}]"
            else:
                raise ValueError(f"unsupported field signature {signature.hex()}")
            fields.append({"name": str(field.Name), "offset": offset, "size": size, "type": type_name})
            offset += size
        result = {"metadata_token": f"0x{0x02000000 + index:08x}", "pack": 1, "size": offset, "fields": fields}
        structures[names[index]] = result
        return result

    for index in (0x4D, 0x4E, 0x4F, 0x50, 0x51, 0x53, 0x54, 0x55, 0x73):
        structure(index)
    chosen = {
        "K7.protocol_struct::makefram", "K7.protocol_struct::ContentNorOr",
        "K7.protocol_struct::Link", "K7.protocol_struct::Read", "K7.protocol_struct::Write",
        "K7.protocol_struct::PareData", "K7.protocol_struct::.cctor",
        "K7.Iparse::CRC16CCITT", "K7.ComPort::.ctor", "K7.wfm_progress::ReadBlock",
        "K7.wfm_progress::WriteBlock", "K7.wfm_progress::WriteChannel",
    }

    def integer(inst):
        opcode = inst.opcode.name
        if opcode in ("ldc.i4", "ldc.i4.s"):
            return inst.operand
        if opcode.startswith("ldc.i4."):
            return -1 if opcode.endswith("m1") else int(opcode.rsplit(".", 1)[1])
        return None

    methods = {}
    for i, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
        name = method_owner[i] + "::" + str(row.Name)
        if name not in chosen:
            continue
        instructions = CilMethodBody(CilMethodBodyReaderBytes(pe.get_data(row.Rva))).instructions
        assignments = []
        for previous, current in zip(instructions, instructions[1:]):
            value = integer(previous)
            if current.opcode.name in ("stfld", "stsfld") and value is not None:
                field_index = current.operand.value & 0xFFFFFF
                assignments.append({"field": field_owner[field_index] + "::" + str(pe.net.mdtables.Field.rows[field_index - 1].Name), "value": value})
        methods[name] = {"token": f"0x{0x06000000 + i:08x}", "rva": f"0x{row.Rva:08x}",
                         "instruction_count": len(instructions),
                         "integer_constants": sorted({integer(inst) for inst in instructions if integer(inst) is not None}),
                         "direct_constant_assignments": assignments}
    assert set(methods) == chosen
    xor_field = next(row for row in pe.net.mdtables.FieldRva.rows if row.Field.row_index == 0x90C)
    xor_key = pe.get_data(xor_field.Rva, 16)
    assert xor_key.hex() == "166c14e62e910d402135d5401303e980"
    assert structures["K7.protocol_struct+MSG_CALMS_ParaWriteReq"]["size"] == 144
    assert structures["K7.CPS_CHANNEL_INFO"]["size"] == 64
    assert 128 in methods["K7.wfm_progress::ReadBlock"]["integer_constants"]
    assert 128 in methods["K7.wfm_progress::WriteBlock"]["integer_constants"]
    assert {0x8408, 0xFFFF}.issubset(methods["K7.Iparse::CRC16CCITT"]["integer_constants"])
    return {
        "schema_version": 1, "kind": "static-CPS-metadata-and-selected-IL-crosscheck",
        "assembly_sha256": REFERENCE_SHA256, "assembly_bytes": len(raw),
        "official_package_label": "UV-K10 Pro CPS 0.56",
        "installer_sha256": "ad891682d9e30893d1ae4d579cc7a2d92fd84a2ff9b7c5333a6138e2c21c29ba",
        "extraction_provenance": {
            "format": "Setup Factory 7 archive, PKWARE DCL",
            "recorded_external_result": "11 application entries checked for decompressed length and CRC32; no executable run",
            "performed_by_this_tool": False,
            "note": "This tool only checks the separately supplied extracted assembly; archive CRC checks belong to the preceding extraction workflow.",
        },
        "structures": structures, "methods": methods,
        "xor_key": {"field_token": "0x0400090c", "rva": f"0x{xor_field.Rva:08x}", "hex": xor_key.hex()},
        "findings": {
            "read": "command 707, declared inner length 12, actual 16-byte body; count is UInt16 despite field name u8DataLen",
            "write": "command 709, declared inner length 12, actual 144-byte body, fixed 128-byte data array; count chooses the meaningful prefix",
            "hello": "command 700, declared inner length 4, actual 28-byte body, utc timestamp plus optional APRS-position fields",
            "read_write_chunk_size": 128,
            "write_channel": "999 records; record struct is 64 bytes; only records with raw attribute <7 selected; then writes 1024 bytes at 0x11000",
            "request_crc": "CRC16CCITT is the method name; code parameters are reflected 0x8408, initial 0xffff, final complement (CRC16/X25)",
            "serial_configuration": "ComPort constructor uses 38400, 8 data bits, StopBits.One and RTS disabled; no physical serial timing measured",
            "response_parser": "PareData checks framing and deobfuscates body; does not calculate a response CRC in this method",
        },
        "limitations": "Selected static paths only; no CLR execution, no CPS runtime or device capture, no verification that every UI mode uses the same path.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assembly", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = analyze(args.assembly.read_bytes())
        text = json.dumps(report, indent=2) + "\n"
        if args.output:
            args.output.write_text(text, encoding="utf-8")
            print(json.dumps({"output": str(args.output), "methods": len(report["methods"]),
                              "structures": len(report["structures"]), "vendor_code_executed": False}))
        else:
            print(text, end="")
    except (OSError, ValueError, AssertionError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
