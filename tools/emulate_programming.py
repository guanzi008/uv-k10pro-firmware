#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Replay stock programming parser, dispatcher, handlers and framing offline.

Requires the exact, user-supplied decoded 8.02.16 image and Unicorn. No vendor
binary is distributed. The experiment supplies ring indices and SRAM flags;
the actual main loop, interrupts, DMA, USB peripheral, UART, RTC, RF and physical
Flash are not emulated. Storage hooks either capture calls or use the separate,
explicit synthetic-NOR roundtrip experiment; neither accesses real hardware.
"""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import struct

from emulate_stock import StockEmulator, EmulationError, NorModel, SCRATCH_BASE, APP_BASE
import programming_protocol as protocol

REQUEST_BUFFER = 0x200004C4
RINGS = {0: (0x20000564, 2060), 1: (0x20001D9E, 384), 2: (0x200022F8, 200)}
SESSION = 0x12345678
ACCEPTED_FLAG = SCRATCH_BASE + 0x800
CRC_INPUT = SCRATCH_BASE + 0x1000


def _new(raw, *, xor_state=0, state=None):
    emulator = StockEmulator(raw)
    emulator.initialize_data()
    state = state or protocol.ProgrammingState(SESSION)
    emulator.put_u32(0x200000C0, state.session)
    emulator.write(0x200000C5, bytes([xor_state]))
    emulator.write(0x200001E0, bytes([state.access_state]))
    emulator.write(0x200000BB, bytes([state.secondary_state]))
    emulator.write(0x2000008D, bytes([state.protect_17000]))
    # Known state avoids an incidental UI redraw; UI functions remain explicit
    # hooks if the handler reaches them. It is not a claim about power-on state.
    emulator.write(0x200000A5, b"\x05")
    emulator.write(REQUEST_BUFFER, b"\x5a" * 160)
    return emulator


def _experiment_frame(body, *, obfuscated=False, transport=1):
    """Allow deliberately inconsistent inner lengths only in this experiment."""
    payload = body + struct.pack("<H", protocol.crc16_x25(body))
    if obfuscated and transport != 2:
        payload = protocol.xor_payload(payload)
    return protocol.START + struct.pack("<H", len(body)) + payload + protocol.END


def _run_parser(emulator, frame, transport, *, start=0, visible=None):
    ring, capacity = RINGS[transport]
    if len(frame) >= capacity:
        raise ValueError("synthetic test frame must not fill or overwrite its ring")
    for offset, octet in enumerate(frame):
        emulator.write(ring + (start + offset) % capacity, bytes([octet]))
    emulator.write(ACCEPTED_FLAG, b"\0")
    visible = len(frame) if visible is None else visible
    result = emulator.call(0x0800E758, (ring, capacity, start, (start + visible) % capacity),
                           stack_args=(REQUEST_BUFFER, ACCEPTED_FLAG, transport))
    return result, bool(emulator.read(ACCEPTED_FLAG, 1)[0])


def _attach_transport_capture(emulator, chunks):
    def uart(machine):
        chunks.append(machine.read(machine.reg(0), machine.reg(1)))

    def usb(machine):
        if machine.reg(0) != 0x81:
            raise EmulationError("unexpected USB endpoint")
        chunks.append(machine.read(machine.reg(1), machine.reg(2)))

    emulator.hook_function(0x0801A890, uart, name="USART1_raw_tx_capture")
    emulator.hook_function(0x0801AAD0, uart, name="auxiliary_UART_raw_tx_capture")
    emulator.hook_function(0x0801D7F8, usb, name="USB_endpoint_81_capture")


def parser_case(raw, *, transport=1, obfuscated=False, start=0,
                corrupt=False, visible=None):
    # Zeroed optional tail ensures the parser input itself is fully supplied.
    # CPS 0.56 retains declared inner length 4 while serializing this 28-byte
    # body. Only the receiver parser is run; the RTC-setting handler is not.
    hello = protocol.Message(protocol.HELLO, struct.pack("<II", SESSION, 0) + bytes(16), 4).encode()
    frame = bytearray(_experiment_frame(hello, obfuscated=obfuscated, transport=transport))
    if corrupt:
        frame[-4] ^= 1
    # Exercise auto-detection from the opposite XOR mode on transports 0/1.
    emulator = _new(raw, xor_state=int(not obfuscated))
    result, accepted = _run_parser(emulator, bytes(frame), transport, start=start, visible=visible)
    output = emulator.read(REQUEST_BUFFER, len(hello))
    expected_accepted = not corrupt and (visible is None or visible >= len(frame))
    assert accepted == expected_accepted
    if accepted:
        assert output == hello
    return {
        "transport": transport, "input_hex": bytes(frame).hex(), "start_index": start,
        "visible_bytes": len(frame) if visible is None else visible,
        "accepted": accepted, "next_index": result.r0,
        "xor_state_after": emulator.read(0x200000C5, 1)[0],
        "output_matches_body": output == hello, "execution": asdict(result),
    }


def chain_case(raw, body, *, transport=1, obfuscated=False, state=None,
               start=0, corrupt=False, compare_model=True):
    state = state or protocol.ProgrammingState(SESSION)
    emulator = _new(raw, xor_state=int(obfuscated), state=state)
    operations, chunks = [], []
    _attach_transport_capture(emulator, chunks)
    emulator.hook_function(0x08006D20, lambda machine: 0, name="programming_UI_noop")
    emulator.hook_function(0x080144B8, lambda machine: 0, name="redraw_UI_noop")
    emulator.hook_function(0x0801D000, lambda machine: 0, name="delay_noop_not_wall_time")
    emulator.hook_function(0x0800F288, lambda machine: 0, name="special_area_reload_noop")

    def read(machine):
        address, destination, count = (machine.reg(i) for i in range(3))
        data = bytes((address + i) & 255 for i in range(count))
        machine.write(destination, data)
        operations.append({"operation": "read", "address": address, "count": count,
                           "data_hex": data.hex()})

    def write(machine):
        address, source, count, mode = (machine.reg(i) for i in range(4))
        operations.append({"operation": "write-plan", "address": address, "count": count,
                           "update_mode": mode, "source_pointer": f"0x{source:08x}",
                           "data_hex": machine.read(source, count).hex()})

    emulator.hook_function(0x080195E4, read, name="synthetic_NOR_read_pattern")
    emulator.hook_function(0x08019694, write, name="RMW_call_capture_no_write")
    frame = bytearray(_experiment_frame(body, obfuscated=obfuscated, transport=transport))
    if corrupt:
        frame[-4] ^= 1
    parse_result, accepted = _run_parser(emulator, bytes(frame), transport, start=start)
    dispatch_result = None
    if accepted:
        # Reproduce the observed caller's accepted-flag branch. The actual
        # dispatcher and every unhooked instruction below it execute.
        dispatch_result = emulator.call(0x08003468, (transport,))
    output = b"".join(chunks)
    model_status = "not-compared-malformed-input" if not compare_model else "matched"
    if compare_model:
        if corrupt:
            assert not accepted and not operations and not output
        else:
            expected = protocol.ProgrammingModel(bytes(range(256)) * 8192).dispatch(body, state)
            if expected.response is None:
                assert not output
            else:
                wire = protocol.encode_frame(expected.response, direction="response",
                                             transport=transport, obfuscated=obfuscated)
                assert output == wire, (output.hex(), wire.hex())
            assert bool(operations) == (expected.operation is not None)
            if expected.operation:
                observed = operations[0]
                planned = expected.operation
                assert (observed["address"], observed["count"], observed["data_hex"]) == (
                    planned.address, planned.count, planned.data.hex())
                if planned.update_mode is not None:
                    assert observed["update_mode"] == planned.update_mode
            reload_seen = any(event["function"] == "special_area_reload_noop" for event in emulator.events)
            assert reload_seen == expected.reload_special_area
    return {
        "transport": transport, "xor": bool(obfuscated and transport != 2),
        "request_body_hex": body.hex(), "request_frame_hex": bytes(frame).hex(),
        "state": asdict(state), "start_index": start, "accepted_by_parser": accepted,
        "parser_execution": asdict(parse_result),
        "dispatcher_execution": None if dispatch_result is None else asdict(dispatch_result),
        "storage_calls": operations, "response_frame_hex": output.hex(),
        "independent_model": model_status, "explicit_hooks_called": emulator.events,
    }


def sender_case(raw, *, transport, obfuscated):
    body = bytes.fromhex("c602040000100000")
    emulator = _new(raw, xor_state=int(obfuscated))
    emulator.write(REQUEST_BUFFER, body)
    chunks = []
    _attach_transport_capture(emulator, chunks)
    result = emulator.call(0x0800361C, (transport, REQUEST_BUFFER, len(body)))
    wire = b"".join(chunks)
    assert wire == protocol.encode_frame(body, direction="response", transport=transport,
                                         obfuscated=obfuscated)
    decoded = protocol.decode_frame(wire, direction="response", transport=transport,
                                    obfuscated=obfuscated)
    return {"transport": transport, "xor_state": int(obfuscated), "wire_hex": wire.hex(),
            "checksum_status": decoded.checksum_status, "execution": asdict(result),
            "explicit_hooks_called": emulator.events}


def extract_usb_descriptors(raw):
    """Parse the static descriptor registered by 0x0801cf24, not enumeration."""
    address = 0x0801DF94
    offset = address - APP_BASE
    device = raw[offset:offset + 18]
    assert device[0:2] == b"\x12\x01"
    config = offset + 18
    assert raw[config:config + 2] == b"\x09\x02"
    total = struct.unpack_from("<H", raw, config + 2)[0]
    position, interfaces, endpoints = config, [], []
    interface = None
    while position < config + total:
        length, kind = raw[position:position + 2]
        assert length >= 2 and position + length <= config + total
        entry = raw[position:position + length]
        if kind == 4:
            interface = entry[2]
            interfaces.append({"number": interface, "alternate": entry[3],
                               "endpoint_count": entry[4], "class": entry[5],
                               "subclass": entry[6], "protocol": entry[7]})
        if kind == 5:
            endpoints.append({"interface": interface, "address": f"0x{entry[2]:02x}",
                              "transfer_type": {2: "bulk", 3: "interrupt"}.get(entry[3] & 3),
                              "max_packet": struct.unpack_from("<H", entry, 4)[0],
                              "interval": entry[6]})
        position += length
    strings = []
    for index in range(4):
        length, kind = raw[position:position + 2]
        assert kind == 3 and length >= 2
        value = raw[position + 2:position + length]
        strings.append({"index": index, "language_ids": [struct.unpack("<H", value)[0]]}
                       if index == 0 else {"index": index, "text": value.decode("utf-16le")})
        position += length
    return {
        "kind": "static-descriptor-parse-not-physical-enumeration", "address": f"0x{address:08x}",
        "registration_caller": "0x0801cf24", "usb_bcd": f"0x{struct.unpack_from('<H', device, 2)[0]:04x}",
        "device_class": device[4], "device_subclass": device[5], "device_protocol": device[6],
        "endpoint_zero_max_packet": device[7], "vid": f"0x{struct.unpack_from('<H', device, 8)[0]:04x}",
        "pid": f"0x{struct.unpack_from('<H', device, 10)[0]:04x}",
        "device_bcd": f"0x{struct.unpack_from('<H', device, 12)[0]:04x}",
        "configuration_length": total, "interfaces": interfaces, "endpoints": endpoints,
        "strings": strings, "interpretation": "CDC ACM communication interface plus CDC data interface",
    }


def synthetic_nor_roundtrip(raw):
    """Connect real protocol dispatch to real RMW over the three NOR hooks.

    This separate experiment keeps the earlier write-call-capture cases intact.
    Channel metadata comes from the independently implemented offline codec;
    all initial storage and every changed byte are artificial experiment data.
    """
    from channel_codec import ChannelRecord, Tone, channel_offset, attribute_offset

    index = 77  # Explicitly zero based; not a claim about a UI's numbering.
    address, attribute_address = channel_offset(index), attribute_offset(index)
    record = ChannelRecord(bytes(64)).with_updates(
        rx_frequency_hz=145_500_000, offset_hz=600_000, offset_direction=2,
        name="MODEL77", bandwidth=1, power=0, modulation=0, squelch=4,
        step_index=4, rx_tone=Tone(1, 885), tx_tone=Tone(0, 0))
    attribute = b"\x02"  # Explicit raw synthetic byte; no automatic band derivation.
    emulator = _new(raw)
    nor = NorModel()
    nor.attach(emulator)
    chunks, transactions = [], []
    _attach_transport_capture(emulator, chunks)
    emulator.hook_function(0x08006D20, lambda machine: 0, name="programming_UI_noop")
    emulator.hook_function(0x080144B8, lambda machine: 0, name="redraw_UI_noop")
    emulator.hook_function(0x0800F288, lambda machine: 0, name="special_area_reload_noop")
    # Non-erased neighbors force the real RMW through erase and page rewriting.
    sector, attribute_sector = address & ~0xFFF, attribute_address & ~0xFFF
    nor.data[sector:sector + 4096] = bytes((i * 13 + 7) & 255 for i in range(4096))
    nor.data[attribute_sector:attribute_sector + 4096] = bytes((i * 17 + 3) & 255 for i in range(4096))
    before = bytes(nor.data)
    expected = bytearray(before)

    def transaction(label, body):
        chunks.clear()
        operation_start, event_start = len(nor.operations), len(emulator.events)
        wire = protocol.encode_frame(body, transport=1)
        parsed, accepted = _run_parser(emulator, wire, 1)
        assert accepted
        dispatched = emulator.call(0x08003468, (1,))
        response = b"".join(chunks)
        decoded = protocol.decode_frame(response, direction="response", transport=1)
        assert not decoded.crc_verified
        transactions.append({
            "name": label, "request_body_hex": body.hex(), "request_frame_hex": wire.hex(),
            "response_body_hex": decoded.message.encode().hex(), "response_frame_hex": response.hex(),
            "response_checksum_status": decoded.checksum_status,
            "parser_execution": asdict(parsed), "dispatcher_execution": asdict(dispatched),
            "nor_operations": [dict(item) for item in nor.operations[operation_start:]],
            "explicit_hooks_called": emulator.events[event_start:],
            "nor_sha256_after": hashlib.sha256(nor.data).hexdigest(),
        })
        return decoded.message

    response = transaction("write_channel_record", protocol.build_write(address, record.raw, SESSION))
    assert response.encode() == protocol.Message(protocol.WRITE_REPLY, struct.pack("<I", address)).encode()
    expected[address:address + 64] = record.raw
    assert nor.data == expected
    assert any(item["op"] == "erase" for item in transactions[-1]["nor_operations"])

    response = transaction("write_channel_attribute", protocol.build_write(attribute_address, attribute, SESSION))
    assert response.encode() == protocol.Message(protocol.WRITE_REPLY, struct.pack("<I", attribute_address)).encode()
    expected[attribute_address:attribute_address + 1] = attribute
    assert nor.data == expected
    assert any(item["op"] == "erase" for item in transactions[-1]["nor_operations"])

    response = transaction("read_channel_record", protocol.build_read(address, 64, SESSION))
    assert response.command == protocol.READ_REPLY
    assert response.payload == struct.pack("<IHH", address, 64, 0) + record.raw
    assert ChannelRecord(response.payload[8:]) == record

    response = transaction("read_channel_attribute", protocol.build_read(attribute_address, 1, SESSION))
    assert response.payload == struct.pack("<IHH", attribute_address, 1, 0) + attribute
    assert nor.data == expected

    emulator.write(0x200001E0, b"\x01")
    emulator.write(0x200000BB, b"\x01")
    changed_record = record.with_updates(name="SKIPPED")
    response = transaction("gated_write_still_acknowledged",
                           protocol.build_write(address, changed_record.raw, SESSION))
    assert response.encode() == protocol.Message(protocol.WRITE_REPLY, struct.pack("<I", address)).encode()
    assert not transactions[-1]["nor_operations"]
    assert nor.data == expected

    response = transaction("gated_read_zero_reply", protocol.build_read(address, 64, SESSION))
    assert response.payload == struct.pack("<IHH", address, 64, 0) + bytes(64)
    assert not transactions[-1]["nor_operations"]
    assert nor.data == expected

    # Equality over the complete synthetic chip already includes both neighbors
    # and every other region; state this separately for reviewability.
    unaffected = bytearray(nor.data)
    unaffected[address:address + 64] = before[address:address + 64]
    unaffected[attribute_address] = before[attribute_address]
    assert bytes(unaffected) == before
    return {
        "kind": "actual-parser-dispatch-RMW-sender-with-synthetic-NOR-roundtrip",
        "request_write_layout": "CPS 0.56 fixed inner length 12, 128-byte padded data, 144-byte body",
        "channel_index_zero_based": index, "record_address": address,
        "attribute_address": attribute_address, "synthetic_attribute": attribute.hex(),
        "synthetic_record": record.describe(), "synthetic_capacity_bytes": len(nor.data),
        "initial_nor_sha256": hashlib.sha256(before).hexdigest(),
        "final_nor_sha256": hashlib.sha256(nor.data).hexdigest(),
        "all_other_bytes_preserved": True, "write_and_readback_match": True,
        "gated_ack_without_storage_change_verified": True,
        "gated_read_returned_zero_without_NOR_read": True,
        "executed": ["0x0800e758 parser", "0x08003468 dispatcher", "stock read/write handlers",
                     "0x08019694 RMW", "0x080197b2 page splitting", "stock USB sender"],
        "NOR_function_replacements": ["0x080195e4 read", "0x08019588 erase", "0x080197ec page program"],
        "limitations": "Synthetic byte-array persistence only; no physical NOR, power-loss durability, USB enumeration, RF or device programming.",
        "transactions": transactions, "passed_transactions": len(transactions),
    }


def run_experiments(raw):
    emulator = _new(raw)
    emulator.write(CRC_INPUT, b"123456789")
    crc_result = emulator.call(0x08018B58, (CRC_INPUT, 9))
    assert crc_result.r0 == 0x906E
    read = lambda address=0x1000, count=16, session=SESSION: protocol.Message(
        protocol.READ, struct.pack("<IHHI", address, count, 0, session)).encode()
    write = lambda address=0x1000, count=16, session=SESSION, flag=0: protocol.Message(
        protocol.WRITE, struct.pack("<IHBBI", address, count, flag, 0, session)
        + bytes(i & 255 for i in range(count))).encode()
    gated = protocol.ProgrammingState(SESSION, 1, 1)
    protected = protocol.ProgrammingState(SESSION, protect_17000=1)
    cases = {
        "read_usb_plain": (read(), {}),
        "read_usb_xor": (read(), {"obfuscated": True}),
        "read_usb_ring_wrap": (read(), {"start": 380}),
        "read_usart1": (read(), {"transport": 0}),
        "read_auxiliary_uart": (read(), {"transport": 2}),
        "read_128": (read(count=128), {}),
        "read_129_rejected": (read(count=129), {}),
        "read_bad_session": (read(session=9), {}),
        "read_start_at_limit_rejected": (read(address=0x1FF000), {}),
        "read_crosses_start_guard": (read(address=0x1FEFFF), {}),
        "read_gate_zero_reply": (read(), {"state": gated}),
        "read_bad_crc_not_dispatched": (read(), {"corrupt": True}),
        "write_normal": (write(), {}),
        "write_cps_056_fixed_64": (protocol.build_write(0x1000, bytes(range(64)), SESSION), {}),
        "write_cps_056_fixed_128": (protocol.build_write(0x1000, bytes(range(128)), SESSION), {}),
        "write_129": (write(count=129), {}),
        "write_mode_1": (write(address=0x50000), {}),
        "write_bad_session": (write(session=9), {}),
        "write_start_at_limit_rejected": (write(address=0x1FF000), {}),
        "write_crosses_start_guard": (write(address=0x1FEFFF), {}),
        "write_gate_ack_without_write": (write(), {"state": gated}),
        "write_17000_guarded": (write(address=0x17000), {"state": protected}),
        "write_17000_flag_1": (write(address=0x17000, flag=1), {"state": protected}),
        "write_18000_reload": (write(address=0x18000), {}),
        "write_18000_secondary_gate": (write(address=0x18000),
                                       {"state": protocol.ProgrammingState(SESSION, secondary_state=1)}),
    }
    chains = {name: chain_case(raw, body, **options) for name, (body, options) in cases.items()}
    # The full body has a valid outer CRC, but the inner length is deliberately
    # wrong. This exercises the parser->dispatcher chain, not a handler alone.
    wrong_inner = write()[:2] + b"\0\0" + write()[4:]
    result = chain_case(raw, wrong_inner, compare_model=False)
    assert result["accepted_by_parser"] and result["storage_calls"][0]["count"] == 16
    chains["synthetic_inner_length_zero"] = result
    # Only four bytes of data exist on the wire, while count declares sixteen.
    # The parser copies the two CRC bytes as well; the remainder is the known
    # 0x5a initialization pattern, not an unexplained real-device memory leak.
    short_data = protocol.Message(protocol.WRITE,
                                  struct.pack("<IHHI", 0x1000, 16, 0, SESSION) + bytes(range(4))).encode()
    result = chain_case(raw, short_data, compare_model=False)
    expected = bytes(range(4)) + struct.pack("<H", protocol.crc16_x25(short_data)) + b"\x5a" * 10
    assert result["accepted_by_parser"]
    assert result["storage_calls"][0]["data_hex"] == expected.hex()
    result["synthetic_stale_buffer_explanation"] = (
        "4 supplied data bytes + 2 copied checksum bytes + 10 initialized 0x5a bytes; "
        "RMW is a capture hook and no storage is changed")
    chains["synthetic_count_data_mismatch"] = result
    parsers = {}
    for name, options in {
        "hello_usb_plain_autodetect": {},
        "hello_usb_xor_autodetect": {"obfuscated": True},
        "hello_usb_wrap": {"start": 380},
        "hello_usart1_plain_autodetect": {"transport": 0},
        "hello_usart1_xor_autodetect": {"transport": 0, "obfuscated": True},
        "hello_auxiliary_plain": {"transport": 2},
        "hello_bad_crc": {"corrupt": True},
        "hello_incomplete": {"visible": 35},
    }.items():
        parsers[name] = parser_case(raw, **options)
    senders = [sender_case(raw, transport=transport, obfuscated=obfuscated)
               for transport in range(3) for obfuscated in (False, True)]
    return {
        "schema_version": 1, "sample_sha256": emulator.sample_sha256,
        "sample_base": "0x08002000", "sample_version": "8.02.16",
        "kind": "actual-stock-instruction-replay-with-explicit-function-hooks",
        "limits": {
            "hardware_validated": False, "physical_IO": False,
            "MMIO": "unmapped; unintended peripheral access stops the experiment",
            "clock_interrupt_DMA_USB_UART_RF": "not modeled",
            "session_and_gate_state": "synthetically assigned in SRAM",
            "ring": "synthetic frame bytes and read/producer indices at observed stock ring addresses",
            "dispatch_link": "host reproduces caller accepted-flag branch between actual parser and actual dispatcher",
            "read_backing": "core cases: synthetic byte value (address + offset) & 255; separate NOR roundtrip uses its byte array",
            "write_backing": "core cases: RMW call capture only; separate NOR roundtrip mutates only its synthetic byte array",
            "request_buffer_tail": "160-byte observed gap initialized to 0x5a; not an allocation-size claim",
            "malformed_cases": "function-level observations, not a physical exploitability assessment",
        },
        "crc_check": {"input_ascii": "123456789", "result": crc_result.r0,
                      "function": "0x08018b58", "execution": asdict(crc_result)},
        "static_usb_descriptors": extract_usb_descriptors(raw),
        "synthetic_nor_roundtrip": synthetic_nor_roundtrip(raw),
        "parser_cases": parsers, "parser_dispatch_cases": chains, "sender_cases": senders,
        "case_counts": {"crc": 1, "parser": len(parsers), "parser_dispatch": len(chains), "sender": len(senders)},
        "all_assertions_passed": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path, help="decoded reference application, never bundled")
    parser.add_argument("--output", type=Path, help="write the reproducible JSON report locally")
    args = parser.parse_args(argv)
    try:
        report = run_experiments(args.raw.read_bytes())
        text = json.dumps(report, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text)
            print(json.dumps({"output": str(args.output), "case_counts": report["case_counts"],
                              "synthetic_nor_roundtrip_transactions": report["synthetic_nor_roundtrip"]["passed_transactions"],
                              "all_assertions_passed": True}, indent=2))
        else:
            print(text, end="")
    except (OSError, EmulationError, ValueError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
