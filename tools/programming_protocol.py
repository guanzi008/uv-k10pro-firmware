#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Offline UV-K10 Pro 8.02.16 programming-frame codec and dispatcher model.

There is deliberately no serial, USB, Bluetooth, radio, or network I/O here.
The request builders impose conservative bounds. ProgrammingModel describes
the observed stock dispatch decisions, including silent failures and ACKs for
skipped writes; it returns write plans and never changes a supplied snapshot.
See docs/programming-protocol.md for evidence and unresolved hardware details.
"""

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import struct

START = b"\xab\xcd"
END = b"\xdc\xba"
XOR_KEY = bytes.fromhex("16 6c 14 e6 2e 91 0d 40 21 35 d5 40 13 03 e9 80")
HELLO = 0x02BC
HELLO_REPLY = 0x02BD
READ = 0x02C3
READ_REPLY = 0x02C4
WRITE = 0x02C5
WRITE_REPLY = 0x02C6
FLASH_START_LIMIT = 0x001FF000
CONSERVATIVE_CHUNK = 128
RING_CAPACITIES = {0: 2060, 1: 384, 2: 200}


class ProtocolError(ValueError):
    pass


def _uint(value, bits, field):
    if not isinstance(value, int) or not 0 <= value < 1 << bits:
        raise ProtocolError(f"{field} must be an unsigned {bits}-bit integer")
    return value


def _transport(value):
    if value not in RING_CAPACITIES:
        raise ProtocolError("transport must be 0 (USART1), 1 (USB), or 2 (auxiliary UART)")
    return value


def crc16_x25(data: bytes) -> int:
    """Reflected 0x1021, initial 0xffff, final xor 0xffff; check=0x906e."""
    crc = 0xFFFF
    for octet in data:
        crc ^= octet
        for _ in range(8):
            crc = (crc >> 1) ^ (0x8408 if crc & 1 else 0)
    return crc ^ 0xFFFF


def xor_payload(data: bytes) -> bytes:
    """XOR body plus checksum, restarting the 16-byte key at body offset zero."""
    return bytes(octet ^ XOR_KEY[i % len(XOR_KEY)] for i, octet in enumerate(data))


@dataclass(frozen=True)
class Message:
    command: int
    payload: bytes
    declared_length: int | None = None

    @property
    def actual_payload_length(self) -> int:
        return len(self.payload)

    @property
    def header_length_field(self) -> int:
        return len(self.payload) if self.declared_length is None else self.declared_length

    @property
    def length_convention(self) -> str:
        if self.command == WRITE and self.header_length_field == 12 and len(self.payload) == 140:
            return "cps-0.56-fixed-128-byte-data-array"
        if self.command == HELLO and self.header_length_field == 4 and len(self.payload) == 24:
            return "cps-0.56-hello-legacy-length"
        return "complete-payload-length"

    def encode(self) -> bytes:
        _uint(self.command, 16, "command")
        _uint(self.header_length_field, 16, "declared length")
        return struct.pack("<HH", self.command, self.header_length_field) + self.payload


def parse_message(body: bytes) -> Message:
    """Parse observed length conventions without trusting declared data sizes.

    CPS 0.56 writes declare the fixed 12-byte request header but append a fixed
    128-byte data array. Its hello declares 4 while serializing 24 payload bytes.
    Compact, self-consistent experimental bodies are also accepted. A write's
    count must never exceed its actual supplied data, regardless of convention.
    """
    if len(body) < 4:
        raise ProtocolError("body is shorter than its four-byte command header")
    command, declared_length = struct.unpack_from("<HH", body)
    payload = body[4:]
    if command == WRITE:
        if len(payload) < 12:
            raise ProtocolError("write request header is incomplete")
        count = struct.unpack_from("<H", payload, 4)[0]
        if count > len(payload) - 12:
            raise ProtocolError("count does not match available write data: declared count exceeds supplied data")
        fixed_cps = declared_length == 12 and len(payload) == 140
        compact = declared_length == len(payload) and len(payload) == 12 + count
        if not (fixed_cps or compact):
            raise ProtocolError("unrecognized write length convention; expected CPS fixed-128 or compact data")
    elif command == HELLO and declared_length == 4 and len(payload) == 24:
        pass  # CPS 0.56 legacy declaration, independently observed in its IL.
    elif declared_length != len(payload):
        raise ProtocolError("inner payload length does not match body length")
    return Message(command, payload, declared_length)


@dataclass(frozen=True)
class DecodedFrame:
    message: Message
    transport: int
    direction: str
    obfuscated: bool
    checksum: int
    checksum_status: str

    @property
    def crc_verified(self) -> bool:
        return self.checksum_status == "crc16-x25-verified"


def encode_frame(body: bytes, *, direction="request", transport=1,
                 obfuscated=False) -> bytes:
    """Encode one complete, well-formed frame; never transmits it.

    USB/USART1 stock responses carry 0xffff, not a calculated CRC. Auxiliary
    transport 2 uses a real CRC and ignores the global XOR mode in stock code.
    """
    _transport(transport)
    parse_message(body)
    if direction not in ("request", "response"):
        raise ProtocolError("direction must be request or response")
    _uint(len(body), 16, "body length")
    if len(body) + 8 >= RING_CAPACITIES[transport]:
        raise ProtocolError("frame does not fit conservatively within the observed receive ring")
    checksum = 0xFFFF if direction == "response" and transport != 2 else crc16_x25(body)
    payload = body + struct.pack("<H", checksum)
    if obfuscated and transport != 2:
        payload = xor_payload(payload)
    return START + struct.pack("<H", len(body)) + payload + END


def decode_frame(frame: bytes, *, direction="request", transport=1,
                 obfuscated=False) -> DecodedFrame:
    """Decode exactly one frame, with its direction and transport explicit."""
    _transport(transport)
    if direction not in ("request", "response"):
        raise ProtocolError("direction must be request or response")
    if len(frame) < 12 or frame[:2] != START or frame[-2:] != END:
        raise ProtocolError("invalid frame delimiters or short frame")
    body_length = struct.unpack_from("<H", frame, 2)[0]
    if body_length + 8 != len(frame):
        raise ProtocolError("outer body length does not match frame length")
    payload = frame[4:-2]
    effective_xor = bool(obfuscated and transport != 2)
    if effective_xor:
        payload = xor_payload(payload)
    body, checksum_bytes = payload[:-2], payload[-2:]
    checksum = struct.unpack("<H", checksum_bytes)[0]
    if direction == "response" and transport != 2:
        if checksum != 0xFFFF:
            raise ProtocolError("expected the stock USB/USART1 0xffff response placeholder")
        status = "placeholder-unverified"
    else:
        if checksum != crc16_x25(body):
            raise ProtocolError("CRC16/X25 mismatch")
        status = "crc16-x25-verified"
    return DecodedFrame(parse_message(body), transport, direction, effective_xor,
                        checksum, status)


def _conservative_interval(address: int, count: int):
    _uint(address, 32, "address")
    if not 1 <= count <= CONSERVATIVE_CHUNK:
        raise ProtocolError("offline builders require 1..128 bytes per request")
    if address >= FLASH_START_LIMIT or count > FLASH_START_LIMIT - address:
        raise ProtocolError("the entire requested interval must be below 0x001ff000")


def build_read(address: int, count: int, session: int, *, reserved=0) -> bytes:
    _conservative_interval(address, count)
    _uint(session, 32, "session")
    _uint(reserved, 16, "reserved")
    return Message(READ, struct.pack("<IHHI", address, count, reserved, session)).encode()


def build_write(address: int, data: bytes, session: int, *, flag=0, reserved=0,
                layout="cps-0.56") -> bytes:
    """Construct an offline draft, including the observed raw +10 flag byte.

    Default layout matches the observed CPS 0.56: length field 12 and a fixed
    zero-padded 128-byte data array. ``layout="compact"`` selects the separately
    replayed compact experiment format with a complete-payload length field.
    CPS names the flag u8AdvanceFlag. At address 0x17000, one stock state requires
    flag==1; this tool does not infer an authentication or unlocking operation.
    """
    _conservative_interval(address, len(data))
    _uint(session, 32, "session")
    _uint(flag, 8, "flag")
    _uint(reserved, 8, "reserved")
    if layout not in ("cps-0.56", "compact"):
        raise ProtocolError("write layout must be cps-0.56 or compact")
    payload = struct.pack("<IHBBI", address, len(data), flag, reserved, session)
    if layout == "cps-0.56":
        return Message(WRITE, payload + data.ljust(128, b"\0"), 12).encode()
    return Message(WRITE, payload + data).encode()


@dataclass(frozen=True)
class ProgrammingState:
    session: int
    access_state: int = 0       # SRAM 0x200001e0; semantic name unconfirmed.
    secondary_state: int = 0    # SRAM 0x200000bb; semantic name unconfirmed.
    protect_17000: int = 0      # SRAM 0x2000008d.


@dataclass(frozen=True)
class FlashOperation:
    operation: str
    address: int
    count: int
    data: bytes = b""
    update_mode: int | None = None


@dataclass(frozen=True)
class DispatchResult:
    response: bytes | None
    operation: FlashOperation | None
    reason: str
    reload_special_area: bool = False


class ProgrammingModel:
    """Offline read-snapshot / write-plan model of the two stock handlers.

    Caller provides synthetic state; it is not obtained from a radio. Requests
    must be self-consistent, but stock session/start/count tests are preserved.
    A write plan specifies RMW mode without pretending that a one-to-one byte
    replacement models sector erase, cache, interrupts, or power loss. The
    actual stock RMW can be replayed over NorModel in emulate_stock.py.
    """

    def __init__(self, snapshot: bytes):
        self.snapshot = bytes(snapshot)

    def dispatch(self, body: bytes, state: ProgrammingState) -> DispatchResult:
        message = parse_message(body)
        if message.command not in (READ, WRITE):
            raise ProtocolError("offline storage model only handles 0x02c3 and 0x02c5")
        if len(message.payload) < 12:
            raise ProtocolError("read/write request header is incomplete")
        address, count, flags, session = struct.unpack_from("<IHHI", message.payload)
        expected = 12 + (count if message.command == WRITE else 0)
        cps_padded = message.command == WRITE and message.length_convention == "cps-0.56-fixed-128-byte-data-array"
        if len(message.payload) != expected and not cps_padded:
            raise ProtocolError("count does not match the read/write payload layout")
        if session != state.session:
            return DispatchResult(None, None, "session-mismatch")
        if message.command == READ and count > 128:
            return DispatchResult(None, None, "read-count-above-128")
        if address >= FLASH_START_LIMIT:
            return DispatchResult(None, None, "start-address-out-of-range")
        gate = state.secondary_state if state.access_state == 1 else state.access_state
        if message.command == READ:
            if gate:
                data, operation, reason = bytes(count), None, "read-gated-zero-reply"
            else:
                if address > len(self.snapshot) or count > len(self.snapshot) - address:
                    raise ProtocolError("observed stock read exceeds the supplied snapshot")
                data = self.snapshot[address:address + count]
                operation = FlashOperation("read", address, count, data)
                reason = "read-performed"
            payload = struct.pack("<IHH", address, count, 0) + data
            return DispatchResult(Message(READ_REPLY, payload).encode(), operation, reason)
        response = Message(WRITE_REPLY, struct.pack("<I", address)).encode()
        if gate:
            return DispatchResult(response, None, "write-gated-ack")
        if address == 0x18000 and state.secondary_state:
            return DispatchResult(response, None, "special-18000-gated-ack")
        if address == 0x17000 and state.protect_17000 == 1 and flags & 0xFF != 1:
            return DispatchResult(response, None, "special-17000-gated-ack")
        data = message.payload[12:12 + count]
        mode = int(address >= 0x50000)
        operation = FlashOperation("write-plan", address, count, data, mode)
        return DispatchResult(response, operation, "write-planned", address == 0x18000)


def _number(text):
    try:
        return int(text, 0)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected an integer, optionally 0x-prefixed") from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    inspect = sub.add_parser("inspect", help="decode one captured frame from a local file")
    inspect.add_argument("frame", type=Path)
    inspect.add_argument("--direction", choices=("request", "response"), required=True)
    read = sub.add_parser("draft-read", help="print a local read-request frame; never transmits")
    read.add_argument("address", type=_number)
    read.add_argument("count", type=_number)
    read.add_argument("--session", type=_number, required=True)
    write = sub.add_parser("draft-write", help="print a local write-request frame; never transmits")
    write.add_argument("address", type=_number)
    write.add_argument("data", type=Path)
    write.add_argument("--session", type=_number, required=True)
    write.add_argument("--flag", type=_number, default=0)
    for child in (inspect, read, write):
        child.add_argument("--transport", type=int, choices=(0, 1, 2), default=1)
        child.add_argument("--xor", action="store_true", help="use the observed XOR16 mode")
    args = parser.parse_args(argv)
    try:
        if args.action == "inspect":
            frame = decode_frame(args.frame.read_bytes(), direction=args.direction,
                                 transport=args.transport, obfuscated=args.xor)
            report = asdict(frame)
            report["message"]["payload"] = frame.message.payload.hex()
            report["message"]["command"] = f"0x{frame.message.command:04x}"
            report["message"]["actual_payload_length"] = frame.message.actual_payload_length
            report["message"]["length_convention"] = frame.message.length_convention
            report["crc_verified"] = frame.crc_verified
        else:
            if args.action == "draft-read":
                body = build_read(args.address, args.count, args.session)
            else:
                body = build_write(args.address, args.data.read_bytes(), args.session, flag=args.flag)
            wire = encode_frame(body, transport=args.transport, obfuscated=args.xor)
            report = {"offline_only": True, "body_hex": body.hex(), "frame_hex": wire.hex(),
                      "transport": args.transport, "hardware_transmission": False}
        print(json.dumps(report, indent=2))
    except (OSError, ProtocolError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
