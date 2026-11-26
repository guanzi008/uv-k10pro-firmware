#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Replay channel experiments against a user-supplied, pinned stock image.

Only NOR read, sector erase and page program are replaced by NorModel. Actual
stock loaders, savers, validators, formatters, memcpy and RMW code execute.
All channel/configuration inputs below are synthetic. No radio I/O occurs.
"""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import struct
import sys

from unicorn import UC_HOOK_CODE
from channel_codec import (
    ChannelRecord, Tone, BYTE_FIELDS, channel_offset, vfo_offset,
    attribute_offset, scan_list_offset, derived_tx_units, ATTRIBUTE_BASE,
)
from emulate_stock import StockEmulator, NorModel, EmulationError, SCRATCH_BASE

LOAD = 0x0800EA24
SAVE = 0x08010130
ATTRIBUTE_UPDATE = 0x080102D8
ATTRIBUTE_GET = 0x080076B4
VALID = 0x080062DC
TONE_SANITIZE = 0x08003B18
TX_FREQUENCY = 0x080059EC
MENU_GET = 0x08007578
MENU_FORMAT = 0x08013670
SCAN_LOAD = 0x0800EE94
SCAN_SAVE = 0x080102B8
RUNTIME = 0x20002AF4


def require(condition, message):
    if not condition:
        raise EmulationError(message)


def setup(raw):
    emulator = StockEmulator(raw)
    emulator.initialize_data()
    nor = NorModel()
    nor.attach(emulator)
    return emulator, nor


def band_conditions(emulator):
    # Explicit fixture conditions; not boot-time discovery or RF configuration.
    emulator.write(0x2000019B, b"\1" * 7)
    emulator.write(0x20002CA4, emulator.read(0x0801E670, 28))
    emulator.write(0x20002CC0, emulator.read(0x0801E68C, 28))
    emulator.write(0x2000030C, b"\1")


def synthetic_record():
    data = bytes((i * 3 + 5) & 255 for i in range(64))
    return ChannelRecord(data).with_updates(
        rx_frequency_hz=145_525_000, offset_hz=600_000,
        aprs_ptt=1, aprs_tx_channel=3, offset_direction=1, bandwidth=1,
        step_index=4, scrambler=3, power=2, busy_lockout=1, reverse=0,
        signaling_decode=1, ptt_id=2, modulation=0, scan_list=255, squelch=4,
        signaling_system=1, aprs_receive=1, name="ABCDEFGHIJ",
        rx_tone=Tone(1, 885), tx_tone=Tone(2, 0o23),
        scan_start_hz=137_000_000, scan_end_hz=173_000_000)


def observe_rmw(emulator):
    calls = []

    def callback(uc, address, size, user_data):
        del uc, size, user_data
        if address == 0x08019694:
            calls.append({"address": emulator.reg(0), "length": emulator.reg(2),
                          "mode": emulator.reg(3)})

    emulator.uc.hook_add(UC_HOOK_CODE, callback)
    return calls


def save_cases(raw):
    results = []
    cases = ((77, 0, 2), (77, 0, 3), (77, 0, 4), (77, 0, 0),
             (0, 0, 2), (998, 1, 2), (999, 0, 0), (999, 1, 4),
             (1005, 0, 2), (1005, 1, 2), (1006, 0, 2))
    for index, side, mode in cases:
        emulator, nor = setup(raw)
        calls = observe_rmw(emulator)
        runtime = bytearray(synthetic_record().raw + bytes(60))
        struct.pack_into("<I", runtime, 0, 0xDEADBEEF)
        struct.pack_into("<I", runtime, 0x40, 14_552_500)
        runtime[0x71] = 2 if index < 999 else min(6, index - 999)
        emulator.write(SCRATCH_BASE, bytes(runtime))
        if index < 1006:
            offset = channel_offset(index) if index < 999 else vfo_offset(index - 999, side)
            nor.data[offset:offset + 64] = b"\xa6" * 64
        result = emulator.call(SAVE, (index, side, SCRATCH_BASE, mode))
        if index >= 1006 or (index < 999 and mode not in (2, 3, 4)):
            require(not calls and not nor.operations, "invalid/unused save mode changed NOR")
        else:
            offset = channel_offset(index) if index < 999 else vfo_offset(index - 999, side)
            if index >= 999 or mode == 2:
                expected = bytearray(runtime[:64])
                struct.pack_into("<I", expected, 0, 14_552_500)
                require(nor.data[offset:offset + 64] == expected, "full-save payload differs")
                require(nor.data[attribute_offset(index)] == runtime[0x71], "save attribute differs")
                require(calls[0] == {"address": offset, "length": 64, "mode": 0}, "full-save call differs")
            else:
                start, size = (0x14, 4) if mode == 3 else (0x18, 16)
                expected = bytearray(b"\xa6" * 64)
                expected[start:start + size] = runtime[start:start + size]
                require(nor.data[offset:offset + 64] == expected, "partial save changed other fields")
                require(calls == [{"address": offset + start, "length": size, "mode": 0}],
                        "partial-save call differs")
        results.append({"index": index, "side": side, "mode": mode,
                        "rmw_calls": calls, "execution": asdict(result)})
    return results


def delete_and_attribute_cases(raw):
    emulator, nor = setup(raw)
    calls = observe_rmw(emulator)
    record = synthetic_record().raw
    nor.data[channel_offset(77):channel_offset(77) + 64] = record
    nor.data[ATTRIBUTE_BASE:ATTRIBUTE_BASE + 4096] = b"\xA2" * 4096
    before = bytes(nor.data)
    result = emulator.call(ATTRIBUTE_UPDATE, (77, SCRATCH_BASE, 0))
    expected = bytearray(before)
    expected[attribute_offset(77)] = 255
    require(nor.data == expected, "logical deletion changed bytes other than one attribute")
    deleted = {"index": 77, "only_changed_offset": attribute_offset(77),
               "record_unchanged": True, "rmw_calls": calls[:], "execution": asdict(result)}
    emulator, nor = setup(raw)
    for index in (1, 63, 64, 999, 1005):
        nor.data[attribute_offset(index)] = index % 7
    returned = []
    for index in (1, 63, 1, 64, 999, 1005):
        value = emulator.call(ATTRIBUTE_GET, (index,)).r0
        require(value == nor.data[attribute_offset(index)], "attribute cache returned wrong byte")
        returned.append({"index": index, "attribute": value})
    reads = [op for op in nor.operations if op["op"] == "read"]
    require(reads == [{"op": "read", "address": 0x11000 + block, "length": 64}
                     for block in (0, 64, 960)], "attribute cache block behavior differs")
    emulator, nor = setup(raw)
    band_conditions(emulator)
    validity = []
    for index, attribute, enabled, expected in (
            (0, 2, 1, 1), (77, 0xA2, 1, 1), (998, 6, 1, 1),
            (999, 2, 1, 0), (77, 7, 1, 0), (77, 255, 1, 0), (77, 2, 0, 0)):
        nor.data[attribute_offset(index)] = attribute
        emulator.write(0x20000084, b"\xff\xff")
        emulator.write(0x2000019B + (attribute & 15 if (attribute & 15) < 7 else 0), bytes([enabled]))
        value = emulator.call(VALID, (index,)).r0
        require(value == expected, "validity test differs")
        validity.append({"index": index, "attribute": attribute, "band_enabled": enabled,
                         "returned": value})
    return {"deletion": deleted, "cache": {"values": returned, "nor_reads": reads},
            "validity": validity}


def load_once(raw, record, *, reverse=0, turbo=1):
    emulator, nor = setup(raw)
    band_conditions(emulator)
    emulator.write(0x2000030C, bytes([turbo]))
    channel = 77
    if reverse is not None:
        record = record.with_updates(reverse=reverse)
    offset = channel_offset(channel)
    nor.data[offset:offset + 64] = record.raw
    nor.data[attribute_offset(channel)] = 2
    emulator.write(0x200023D0, struct.pack("<H", channel))
    result = emulator.call(LOAD, (0, 3))
    return emulator, nor, result


def loader_cases(raw):
    results = []
    record = synthetic_record()
    for reverse, turbo in ((0, 1), (1, 1), (0, 0)):
        emulator, nor, result = load_once(raw, record, reverse=reverse, turbo=turbo)
        data = emulator.read(RUNTIME, 124)
        require(emulator.u32(RUNTIME + 0x40) == 14_552_500, "RX frequency changed")
        require(emulator.u32(RUNTIME + 0x4C) == 14_612_500, "TX offset arithmetic changed")
        require(data[0x18:0x22] == b"ABCDEFGHIJ", "name is not 10 bytes")
        require(data[0x22:0x28] == bytes(6), "loader did not clear name allocation tail")
        require(data[0x10] == (2 if turbo else 1), "power capability clamp differs")
        rx_pointer = RUNTIME + (0x4C if reverse else 0x40)
        tx_pointer = RUNTIME + (0x40 if reverse else 0x4C)
        require(emulator.u32(RUNTIME + 0x58) == rx_pointer, "RX reverse pointer differs")
        require(emulator.u32(RUNTIME + 0x5C) == tx_pointer, "TX reverse pointer differs")
        results.append({"reverse": reverse, "turbo_capability_byte": turbo,
                        "execution": asdict(result), "nor_operations": nor.operations,
                        "runtime_fields": {key: data[offset] for key, (offset, _) in BYTE_FIELDS.items()},
                        "rx_units": emulator.u32(RUNTIME + 0x40),
                        "tx_units": emulator.u32(RUNTIME + 0x4C),
                        "rx_pointer": hex(rx_pointer), "tx_pointer": hex(tx_pointer),
                        "name_hex": data[0x18:0x28].hex(),
                        "rx_tone": [data[0x44], emulator.u32(RUNTIME + 0x48)],
                        "tx_tone": [data[0x50], emulator.u32(RUNTIME + 0x54)]})
    invalid = bytearray(b"\xff" * 64)
    struct.pack_into("<I", invalid, 0, 14_552_500)
    emulator, _, result = load_once(raw, ChannelRecord(bytes(invalid)), reverse=None, turbo=1)
    data = emulator.read(RUNTIME, 124)
    fallback = {"aprs_ptt": 0, "aprs_tx_channel": 0, "offset_direction": 0,
                "bandwidth": 0, "step_index": 12, "scrambler": 0, "power": 1,
                "busy_lockout": 0, "reverse": 0, "signaling_decode": 0, "ptt_id": 0,
                "modulation": 0, "scan_list": 255, "squelch": 4,
                "signaling_system": 0, "aprs_receive": 0}
    for key, value in fallback.items():
        require(data[BYTE_FIELDS[key][0]] == value, f"invalid-field fallback differs: {key}")
    require(emulator.u32(RUNTIME + 4) == 1_000_000, "invalid offset fallback differs")
    require(data[0x44] == data[0x50] == 0, "invalid tones were not cleared")
    results.append({"case": "invalid_fields", "execution": asdict(result),
                    "runtime_fields": fallback, "offset_units": 1_000_000,
                    "rx_tone": [0, 0], "tx_tone": [0, 0]})
    emulator, _, result = load_once(raw, record.with_updates(modulation=2))
    require(emulator.read(RUNTIME + 0x44, 1) == b"\0" and
            emulator.read(RUNTIME + 0x50, 1) == b"\0", "non-FM active tones were not disabled")
    require(emulator.read(RUNTIME + 0x6D, 3) == b"\2\0\0", "non-FM signaling flags differ")
    require(emulator.read(RUNTIME + 0x0A, 2) == b"\1\2", "non-FM persisted tone shadows changed")
    results.append({"case": "non_fm_active_settings", "modulation": 2,
                    "active_tone_types": [0, 0], "persisted_tone_shadows": [1, 2],
                    "active_scrambler_and_decode": [0, 0], "execution": asdict(result)})
    emulator, nor, _ = load_once(raw, record)
    nor.data[channel_offset(77) + 0x18:channel_offset(77) + 0x22] = b"1234567890"
    emulator.write(RUNTIME + 0x22, b"uvwxyz")
    before = len(nor.operations)
    result = emulator.call(LOAD, (0, 4))
    require(emulator.read(RUNTIME + 0x18, 16) == b"1234567890\0vwxyz", "name-only load extent differs")
    results.append({"case": "name_only_mode_4", "name_hex": emulator.read(RUNTIME + 0x18, 16).hex(),
                    "nor_operations": nor.operations[before:], "execution": asdict(result)})
    return results


def tone_and_frequency_cases(raw):
    emulator, _ = setup(raw)
    tones = []
    for kind, value in ((0, 0), (0, 99), (1, 599), (1, 600), (1, 885), (1, 2600),
                        (1, 2601), (2, 0), (2, 0x1FF), (2, 0x200), (3, 0o23),
                        (3, 0x200), (4, 0x7FFFFF), (4, 0x800000),
                        (5, 0xFFFFFF), (5, 0x1000000), (6, 1), (255, 0xFFFFFFFF)):
        emulator.write(SCRATCH_BASE, bytes([kind]))
        emulator.put_u32(SCRATCH_BASE + 4, value)
        result = emulator.call(TONE_SANITIZE, (SCRATCH_BASE, SCRATCH_BASE + 4))
        actual = Tone(emulator.read(SCRATCH_BASE, 1)[0], emulator.u32(SCRATCH_BASE + 4))
        expected = Tone(kind, value).sanitized()
        require(actual == expected, "tone sanitizer and independent model disagree")
        tones.append({"input": [kind, value], "output": [actual.kind, actual.value],
                      "instructions": result.instructions})
    frequencies = []
    for rx, offset, direction in ((14_552_500, 60_000, 0), (14_552_500, 60_000, 1),
                                  (14_552_500, 60_000, 2), (2_500_000, 1, 2),
                                  (58_000_000, 1, 1), (2_500_000, 3_000_000, 2),
                                  (0xFFFFFFFF, 3_000_001, 1), (14_552_500, 0, 255)):
        emulator.put_u32(SCRATCH_BASE + 0x40, rx)
        emulator.put_u32(SCRATCH_BASE + 4, offset)
        emulator.write(SCRATCH_BASE + 0x0C, bytes([direction]))
        result = emulator.call(TX_FREQUENCY, (SCRATCH_BASE,))
        actual = emulator.u32(SCRATCH_BASE + 0x4C)
        require(actual == derived_tx_units(rx, offset, direction), "TX arithmetic model differs")
        frequencies.append({"rx_units": rx, "offset_units": offset, "direction": direction,
                            "tx_units": actual, "instructions": result.instructions})
    return {"tone_sanitizer": tones, "tx_arithmetic": frequencies}


def format_menu(emulator, menu, value, tone_kind=0):
    emulator.write(0x20002F1D, bytes([menu]))
    emulator.put_u32(0x20000228, value)
    emulator.write(0x20000221, bytes([tone_kind]))
    en, cn = SCRATCH_BASE + 0x1000, SCRATCH_BASE + 0x1100
    pointers, flag = SCRATCH_BASE + 0x1200, SCRATCH_BASE + 0x1208
    emulator.write(en, bytes(128))
    emulator.write(cn, bytes(128))
    emulator.put_u32(pointers, en)
    emulator.put_u32(pointers + 4, cn)
    emulator.call(MENU_FORMAT, (pointers, pointers + 4, flag))
    return emulator.read(emulator.u32(pointers), 80).split(b"\0", 1)[0].decode("ascii")


def menu_cases(raw):
    emulator, _ = setup(raw)
    emulator.put_u32(0x20000184, RUNTIME)
    emulator.write(RUNTIME, synthetic_record().raw + bytes(60))
    emulator.write(RUNTIME + 0x62, struct.pack("<H", 77))
    results = []
    for name, menu, offset, expected in (
            ("power", 2, 0x10, ("LOW", "HIGH", "TURBO")),
            ("bandwidth", 8, 0x0D, ("WIDE", "NARROW")),
            ("modulation", 11, 0x15, ("FM", "AM", "SSB", "CW")),
            ("offset_direction", 6, 0x0C, ("OFF", "+", "-")),
            ("ptt_id", 13, 0x14, ("OFF", "BOT", "EOT", "BOTH")),
            ("aprs_ptt", 17, 8, ("OFF", "BOT", "EOT")),
            ("signaling_system", 12, 0x3C, ("DTMF", "5TONE"))):
        displays = []
        for value, label in enumerate(expected):
            emulator.write(RUNTIME + offset, bytes([value]))
            emulator.write(0x20002F1D, bytes([menu]))
            emulator.call(MENU_GET)
            actual_value = emulator.u32(0x20000228)
            require(actual_value == value, "menu getter does not select expected field")
            display = format_menu(emulator, menu, actual_value)
            require(display == label, "menu formatter label differs")
            displays.append({"value": value, "display": display})
        results.append({"field": name, "record_offset": offset, "menu_index": menu,
                        "values": displays})
    for menu, value, kind, expected in ((7, 60_000, 0, "0.60000MHz"),
                                       (5, 885, 1, "88.5Hz"), (5, 0o23, 2, "D023N"),
                                       (5, 0o23, 3, "D023I"), (5, 0x123456, 4, "U23:123456"),
                                       (5, 0xABCDEF, 5, "U24:abcdef"),
                                       (15, 32, 0, "ALL CHAN"), (15, 33, 0, "NULL")):
        display = format_menu(emulator, menu, value, kind)
        require(display == expected, "frequency/tone/list UI label differs")
        results.append({"menu_index": menu, "value": value, "tone_kind": kind, "display": display})
    return results


def scan_list_cases(raw):
    emulator, nor = setup(raw)
    band_conditions(emulator)
    emulator.put_u32(0x20000184, RUNTIME)
    emulator.write(RUNTIME + 0x62, struct.pack("<H", 77))
    members = (0, 77, 998, 999, 0xFFFF, 77) + (0xFFFF,) * 42
    record = bytearray((i * 5 + 1) & 255 for i in range(128))
    record[:10] = b"SCAN TEST1"
    struct.pack_into("<2H", record, 0x10, 0x5A5A, 998)
    struct.pack_into("<48H", record, 0x18, *members)
    offset = scan_list_offset(5)
    nor.data[offset:offset + 128] = record
    for channel, band in ((0, 2), (77, 2), (998, 5)):
        nor.data[attribute_offset(channel)] = band
    result = emulator.call(SCAN_LOAD, (5,))
    count = emulator.read(0x20000190, 1)[0]
    output = list(struct.unpack("<4H", emulator.read(0x20002C44, 8)))
    priorities = list(struct.unpack("<2H", emulator.read(0x20000192, 4)))
    require(count == 4 and output == [0, 77, 998, 77], "scan member validation/compaction differs")
    require(priorities == [77, 998], "scan priorities differ")
    require(emulator.read(0x20002C2C, 11) == b"SCAN TEST1\0", "scan name load differs")
    loaded = {"index": 5, "input_member_slots": list(members), "retained_members": output,
              "member_count": count, "input_priorities": [0x5A5A, 998],
              "resolved_priorities": priorities, "execution": asdict(result)}
    before_tail = bytes(nor.data[offset + 120:offset + 128])
    calls = observe_rmw(emulator)
    save_result = emulator.call(SCAN_SAVE, (5,))
    require(calls == [{"address": offset, "length": 120, "mode": 0}], "scan saver extent differs")
    require(nor.data[offset + 120:offset + 128] == before_tail, "scan saver changed reserved tail")
    return {"load": loaded, "save": {"rmw_calls": calls, "tail_8_bytes_preserved": True,
                                      "execution": asdict(save_result)}}


def experiments(raw):
    emulator = StockEmulator(raw)
    return {
        "sample_sha256": emulator.sample_sha256,
        "schema_version": 1,
        "scope": "Actual reference Thumb instructions above explicit NOR byte-array hooks; synthetic records only",
        "hardware_hooks": ["0x080195e4 read", "0x08019588 erase 4 KiB", "0x080197ec page program"],
        "conditions": {"startup": "actual 0x08002ab8 scatter initialization, then controlled globals",
                       "loader": {"selected_index_at_0x200023d0": 77, "side": 0, "mode": 3,
                                  "enabled_bands_at_0x2000019b": [1] * 7,
                                  "dynamic_bounds": "copied from reference 0x0801e670/0x0801e68c",
                                  "attribute": 2},
                       "scratch": "0x21000000 is artificial input RAM, not fitted radio SRAM",
                       "nor_capacity": "2 MiB synthetic input; not a hardware capacity result"},
        "save_modes": save_cases(raw),
        "attributes": delete_and_attribute_cases(raw),
        "loader": loader_cases(raw),
        "pure_functions": tone_and_frequency_cases(raw),
        "stock_ui": menu_cases(raw),
        "scan_lists": scan_list_cases(raw),
        "checks_passed": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path, help="decoded reference application, supplied separately")
    args = parser.parse_args(argv)
    try:
        report = experiments(args.raw.read_bytes())
    except (OSError, ValueError, EmulationError) as error:
        print(f"emulate_channels: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
