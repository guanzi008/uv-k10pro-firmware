#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Lossless, offline UV-K10 Pro 8.02.16 channel and scan-list inspection.

Inputs are external-NOR dump bytes, not the MCU update image. No serial, USB,
Bluetooth, RF, or device-writing operation is provided. Field meanings and
version differences are documented in docs/channel-storage.md.
"""

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
import sys

CHANNEL_COUNT = 999
CHANNEL_SIZE = 64
ATTRIBUTE_BASE = 0x11000
VFO_BASE = 0x10000
VFO_BAND_COUNT = 7
SCAN_LIST_BASE = 0x16000
SCAN_LIST_COUNT = 32
SCAN_LIST_SIZE = 128
SCAN_LIST_MEMBERS = 48
FREQUENCY_UNIT_HZ = 10
STEP_UNITS = (100, 150, 200, 250, 500, 625, 833, 900, 1000, 1250, 1500, 2000, 2500)
POWER_NAMES = ("LOW", "HIGH", "TURBO")
BANDWIDTH_NAMES = ("WIDE", "NARROW")
MODULATION_NAMES = ("FM", "AM", "SSB", "CW")
TONE_NAMES = ("none", "CTCSS", "DCS-N", "DCS-I", "U23", "U24")

# These are byte fields, not packed bitfields. Read operations preserve invalid
# values. Explicit edits validate the particular field being changed only.
BYTE_FIELDS = {
    "aprs_ptt": (0x08, (0, 1, 2)),
    "aprs_tx_channel": (0x09, tuple(range(9))),
    "offset_direction": (0x0C, (0, 1, 2)),
    "bandwidth": (0x0D, (0, 1)),
    "step_index": (0x0E, tuple(range(13))),
    "scrambler": (0x0F, tuple(range(11))),
    "power": (0x10, (0, 1, 2)),
    "busy_lockout": (0x11, (0, 1)),
    "reverse": (0x12, (0, 1)),
    "signaling_decode": (0x13, (0, 1)),
    "ptt_id": (0x14, (0, 1, 2, 3)),
    "modulation": (0x15, (0, 1, 2, 3)),
    "scan_list": (0x16, tuple(range(33)) + (0xFF,)),
    "squelch": (0x17, tuple(range(10))),
    "signaling_system": (0x3C, (0, 1)),
    "aprs_receive": (0x3D, (0, 1)),
}


class ChannelError(ValueError):
    pass


def _integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ChannelError(f"{label} must be an integer in {minimum}..{maximum}")
    return value


def channel_offset(index):
    return _integer(index, 0, CHANNEL_COUNT - 1, "channel index") * CHANNEL_SIZE


def vfo_offset(band, side):
    _integer(band, 0, VFO_BAND_COUNT - 1, "VFO band index")
    _integer(side, 0, 1, "VFO side (0=A, 1=B)")
    return VFO_BASE + band * 128 + side * 64


def attribute_offset(index):
    # The final seven entries are shared by the two VFO sides for each band.
    return ATTRIBUTE_BASE + _integer(index, 0, CHANNEL_COUNT + VFO_BAND_COUNT - 1,
                                      "channel/VFO attribute index")


def scan_list_offset(index):
    return SCAN_LIST_BASE + _integer(index, 0, SCAN_LIST_COUNT - 1,
                                     "scan-list index") * SCAN_LIST_SIZE


def frequency_units(hz):
    _integer(hz, 0, 0xFFFFFFFF * FREQUENCY_UNIT_HZ, "frequency in Hz")
    if hz % FREQUENCY_UNIT_HZ:
        raise ChannelError("frequency must be an exact multiple of 10 Hz")
    return hz // FREQUENCY_UNIT_HZ


def derived_tx_units(rx_units, offset_units, direction):
    """Model the arithmetic at 0x080059EC, before reverse/policy handling.

    This includes its 32-bit wrap and signed clamp to 25..580 MHz. It is not
    permission to transmit and does not model later band/policy checks.
    """
    _integer(rx_units, 0, 0xFFFFFFFF, "RX units")
    _integer(offset_units, 0, 0xFFFFFFFF, "offset units")
    _integer(direction, 0, 255, "direction byte")
    value = rx_units
    if direction == 1:
        value = (rx_units + offset_units) & 0xFFFFFFFF
    elif direction == 2:
        value = (rx_units - offset_units) & 0xFFFFFFFF
    elif direction != 0:
        return 2_500_000
    signed = value if value < 0x80000000 else value - 0x100000000
    return max(2_500_000, min(58_000_000, signed))


def _visible_name(data):
    # FF is an erased sentinel; retained in raw bytes even if display stops.
    end = min((i for i, byte in enumerate(data) if byte in (0, 255)), default=len(data))
    return data[:end].decode("gb2312", errors="backslashreplace")


def _name_bytes(value):
    if isinstance(value, str):
        try:
            value = value.encode("gb2312")
        except UnicodeEncodeError as error:
            raise ChannelError("name is not representable in GB2312") from error
    if not isinstance(value, bytes) or len(value) > 10 or any(x in (0, 255) for x in value):
        raise ChannelError("name must contain at most 10 encoded bytes, without NUL/FF")
    return value.ljust(10, b"\0")


@dataclass(frozen=True)
class Tone:
    kind: int
    value: int

    def valid(self):
        if type(self.kind) is not int or type(self.value) is not int:
            return False
        if self.kind == 0:
            return self.value == 0
        if self.kind == 1:
            return 600 <= self.value <= 2600
        if self.kind in (2, 3):
            return 0 <= self.value <= 0x1FF
        if self.kind == 4:
            return 0 <= self.value <= 0x7FFFFF
        if self.kind == 5:
            return 0 <= self.value <= 0xFFFFFF
        return False

    def sanitized(self):
        """The explicit 0x08003B18 behavior; never applied during parsing."""
        return self if self.valid() else Tone(0, 0)

    def describe(self):
        name = TONE_NAMES[self.kind] if 0 <= self.kind < len(TONE_NAMES) else "unknown"
        result = {"kind": self.kind, "value": self.value, "name": name,
                  "accepted_by_reference_sanitizer": self.valid()}
        if self.kind == 1:
            result["frequency_hz"] = f"{self.value // 10}.{self.value % 10}"
        elif self.kind in (2, 3):
            result["display"] = f"D{self.value:03o}{'N' if self.kind == 2 else 'I'}"
        elif self.kind in (4, 5):
            result["display"] = f"{name}:{self.value:06x}"
        return result


@dataclass(frozen=True)
class ChannelRecord:
    raw: bytes

    def __post_init__(self):
        if not isinstance(self.raw, bytes) or len(self.raw) != CHANNEL_SIZE:
            raise ChannelError("a channel record must be exactly 64 bytes")

    def u32(self, offset):
        return struct.unpack_from("<I", self.raw, offset)[0]

    @property
    def name_bytes(self):
        return self.raw[0x18:0x22]

    @property
    def rx_tone(self):
        return Tone(self.raw[0x0A], self.u32(0x28))

    @property
    def tx_tone(self):
        return Tone(self.raw[0x0B], self.u32(0x2C))

    def with_updates(self, **changes):
        """Edit named fields while preserving every other byte verbatim.

        Numeric field names are those in BYTE_FIELDS. Frequencies use integer
        Hz; rx_tone/tx_tone take Tone objects. Name changes affect ten bytes,
        preserving the remaining six bytes of the 16-byte name allocation.
        """
        result = bytearray(self.raw)
        frequencies = {"rx_frequency_hz": 0, "offset_hz": 4,
                       "scan_start_hz": 0x30, "scan_end_hz": 0x34}
        for key, value in changes.items():
            if key in BYTE_FIELDS:
                offset, allowed = BYTE_FIELDS[key]
                if type(value) is not int or value not in allowed:
                    raise ChannelError(f"invalid value for {key}: {value!r}")
                result[offset] = value
            elif key in frequencies:
                units = frequency_units(value)
                if key == "offset_hz" and units > 99_999_999:
                    raise ChannelError("offset exceeds the reference loader's accepted maximum")
                struct.pack_into("<I", result, frequencies[key], units)
            elif key == "name":
                result[0x18:0x22] = _name_bytes(value)
            elif key in ("rx_tone", "tx_tone"):
                if not isinstance(value, Tone) or not value.valid():
                    raise ChannelError(f"{key} must be a valid Tone")
                type_offset, value_offset = (0x0A, 0x28) if key == "rx_tone" else (0x0B, 0x2C)
                result[type_offset] = value.kind
                struct.pack_into("<I", result, value_offset, value.value)
            else:
                raise ChannelError(f"unknown field: {key}")
        return ChannelRecord(bytes(result))

    def describe(self):
        fields = {key: self.raw[offset] for key, (offset, _) in BYTE_FIELDS.items()}
        power = fields["power"]
        bandwidth = fields["bandwidth"]
        modulation = fields["modulation"]
        step = fields["step_index"]
        return {
            "raw_hex": self.raw.hex(),
            "name": _visible_name(self.name_bytes),
            "name_bytes_hex": self.name_bytes.hex(),
            "rx_frequency_hz": self.u32(0) * 10,
            "offset_hz": self.u32(4) * 10,
            "derived_tx_frequency_hz_before_reverse": derived_tx_units(
                self.u32(0), self.u32(4), fields["offset_direction"]) * 10,
            "derived_frequency_note": "Applies 0x080059EC directly to raw values; loader field/band validation and reverse are not applied.",
            "rx_tone": self.rx_tone.describe(),
            "tx_tone": self.tx_tone.describe(),
            "fields": fields,
            "power_name": POWER_NAMES[power] if power < len(POWER_NAMES) else "unknown",
            "bandwidth_name": BANDWIDTH_NAMES[bandwidth] if bandwidth < 2 else "unknown",
            "modulation_name_8_02_16": MODULATION_NAMES[modulation] if modulation < 4 else "unknown",
            "step_hz": STEP_UNITS[step] * 10 if step < len(STEP_UNITS) else None,
            "vfo_scan_start_hz": self.u32(0x30) * 10,
            "vfo_scan_end_hz": self.u32(0x34) * 10,
            "uninterpreted_hex": {"0x22..0x27": self.raw[0x22:0x28].hex(),
                                  "0x38..0x3b": self.raw[0x38:0x3C].hex(),
                                  "0x3e..0x3f": self.raw[0x3E:0x40].hex()},
        }


@dataclass(frozen=True)
class ScanListRecord:
    raw: bytes

    def __post_init__(self):
        if not isinstance(self.raw, bytes) or len(self.raw) != SCAN_LIST_SIZE:
            raise ChannelError("a scan-list record must be exactly 128 bytes")

    @property
    def member_slots(self):
        return struct.unpack_from("<48H", self.raw, 0x18)

    @property
    def priorities(self):
        return struct.unpack_from("<2H", self.raw, 0x10)

    def with_members(self, members):
        values = tuple(members)
        if len(values) > SCAN_LIST_MEMBERS:
            raise ChannelError("a scan list has at most 48 members")
        for value in values:
            _integer(value, 0, CHANNEL_COUNT - 1, "member channel index")
        result = bytearray(self.raw)
        struct.pack_into("<48H", result, 0x18, *(values + (0xFFFF,) * (48 - len(values))))
        return ScanListRecord(bytes(result))

    def describe(self):
        return {"raw_hex": self.raw.hex(), "name": _visible_name(self.raw[:10]),
                "name_bytes_hex": self.raw[:10].hex(),
                "priorities": list(self.priorities),
                "priority_sentinels": {"65535": "none", "23130": "current channel"},
                "member_slots": list(self.member_slots),
                "uninterpreted_hex": {"0x0a..0x0f": self.raw[10:16].hex(),
                                      "0x14..0x17": self.raw[20:24].hex(),
                                      "0x78..0x7f": self.raw[120:128].hex()}}


def _slice(image, offset, length):
    if offset < 0 or length < 0 or offset + length > len(image):
        raise ChannelError(f"dump is too short for offset 0x{offset:x}, length {length}")
    return bytes(image[offset:offset + length])


def read_channel(image, index):
    return ChannelRecord(_slice(image, channel_offset(index), CHANNEL_SIZE))


def read_attribute(image, index):
    return _slice(image, attribute_offset(index), 1)[0]


def replace_channel(image, index, record, *, attribute=None):
    """Return a modified local dump; no implicit band/attribute derivation."""
    if not isinstance(record, ChannelRecord):
        raise ChannelError("record must be a ChannelRecord")
    offset = channel_offset(index)
    _slice(image, offset, CHANNEL_SIZE)
    result = bytearray(image)
    result[offset:offset + CHANNEL_SIZE] = record.raw
    if attribute is not None:
        _integer(attribute, 0, 255, "attribute")
        where = attribute_offset(index)
        _slice(image, where, 1)
        result[where] = attribute
    return bytes(result)


def delete_channel(image, index):
    """Stock logical deletion: mark its attribute FF; retain all record bytes."""
    channel_offset(index)
    where = attribute_offset(index)
    _slice(image, where, 1)
    result = bytearray(image)
    result[where] = 0xFF
    return bytes(result)


def attribute_description(value):
    _integer(value, 0, 255, "attribute")
    return {"raw": value, "band_index": value & 15,
            "band_nibble_valid": (value & 15) < 7,
            "upper_nibble": value >> 4,
            "note": "normal-channel validity also requires that band to be enabled"}


def changed_regions(before, after):
    if len(before) != len(after):
        raise ChannelError("local dump edits must preserve length")
    result = []
    start = None
    for i, (old, new) in enumerate(zip(before, after)):
        if old != new and start is None:
            start = i
        if old == new and start is not None:
            result.append({"offset": start, "length": i - start,
                           "before_hex": before[start:i].hex(), "after_hex": after[start:i].hex()})
            start = None
    if start is not None:
        result.append({"offset": start, "length": len(before) - start,
                       "before_hex": before[start:].hex(), "after_hex": after[start:].hex()})
    return result


def _number(value):
    return int(value, 0)


def _tone_arg(value):
    try:
        kind, code = value.split(":")
        tone = Tone(_number(kind), _number(code))
    except (ValueError, TypeError) as error:
        raise ChannelError("tone must be TYPE:VALUE, e.g. 1:885 or 2:0o23") from error
    if not tone.valid():
        raise ChannelError("tone is outside the reference sanitizer bounds")
    return tone


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("inspect", "patch", "delete", "inspect-scan-list"):
        command_parser = sub.add_parser(command)
        command_parser.add_argument("image", type=Path)
        command_parser.add_argument("--index", type=_number, required=True,
                                    help="zero-based index")
        if command in ("patch", "delete"):
            command_parser.add_argument("output", type=Path)
            command_parser.add_argument("--force", action="store_true")
        if command == "patch":
            command_parser.add_argument("--name")
            command_parser.add_argument("--rx-hz", type=_number)
            command_parser.add_argument("--offset-hz", type=_number)
            command_parser.add_argument("--rx-tone")
            command_parser.add_argument("--tx-tone")
            command_parser.add_argument("--attribute", type=_number)
            command_parser.add_argument("--set", action="append", default=[], metavar="FIELD=INTEGER",
                                        help="byte field: " + ", ".join(BYTE_FIELDS))
    single = sub.add_parser("inspect-record")
    single.add_argument("image", type=Path)
    vfo = sub.add_parser("inspect-vfo")
    vfo.add_argument("image", type=Path)
    vfo.add_argument("--band", type=_number, required=True)
    vfo.add_argument("--side", choices=("A", "B"), required=True)
    args = parser.parse_args(argv)
    try:
        image = args.image.read_bytes()
        if args.command == "inspect":
            report = read_channel(image, args.index).describe()
            report.update(index=args.index, display_channel_number=args.index + 1,
                          record_offset=channel_offset(args.index),
                          attribute=attribute_description(read_attribute(image, args.index)))
        elif args.command == "inspect-record":
            report = ChannelRecord(image).describe()
        elif args.command == "inspect-vfo":
            offset = vfo_offset(args.band, ("A", "B").index(args.side))
            report = ChannelRecord(_slice(image, offset, CHANNEL_SIZE)).describe()
            report.update(band=args.band, side=args.side, record_offset=offset)
        elif args.command == "inspect-scan-list":
            offset = scan_list_offset(args.index)
            report = ScanListRecord(_slice(image, offset, SCAN_LIST_SIZE)).describe()
            report.update(index=args.index, record_offset=offset)
        else:
            if args.command == "delete":
                updated = delete_channel(image, args.index)
            else:
                changes = {}
                for arg, field in (("name", "name"), ("rx_hz", "rx_frequency_hz"),
                                   ("offset_hz", "offset_hz")):
                    if getattr(args, arg) is not None:
                        changes[field] = getattr(args, arg)
                for side in ("rx", "tx"):
                    value = getattr(args, side + "_tone")
                    if value is not None:
                        changes[side + "_tone"] = _tone_arg(value)
                for assignment in args.set:
                    key, value = assignment.split("=", 1)
                    if key not in BYTE_FIELDS or key in changes:
                        raise ChannelError(f"unknown or duplicate byte field: {key}")
                    changes[key] = _number(value)
                if not changes and args.attribute is None:
                    raise ChannelError("no field changes requested")
                record = read_channel(image, args.index).with_updates(**changes)
                updated = replace_channel(image, args.index, record, attribute=args.attribute)
            with args.output.open("wb" if args.force else "xb") as output:
                output.write(updated)
            report = {"output": str(args.output), "operation": "offline dump edit",
                      "before_sha256": hashlib.sha256(image).hexdigest(),
                      "after_sha256": hashlib.sha256(updated).hexdigest(),
                      "changed_regions": changed_regions(image, updated)}
    except (OSError, ValueError) as error:
        print(f"channel_codec: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
