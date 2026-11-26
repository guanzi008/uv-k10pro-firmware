# SPDX-License-Identifier: MIT
"""Independent synthetic protocol checks; no radio or proprietary sample needed."""

from pathlib import Path
import os
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import programming_protocol as protocol  # noqa: E402


class FrameTests(unittest.TestCase):
    def setUp(self):
        self.ack = bytes.fromhex("c602040000100000")

    def test_x25_standard_check(self):
        self.assertEqual(protocol.crc16_x25(b"123456789"), 0x906E)
        self.assertEqual(protocol.crc16_x25(b""), 0)

    def test_verified_actual_sender_fixtures(self):
        fixtures = {
            (0, False): "abcd0800c602040000100000ffffdcba",
            (0, True): "abcd0800d06e10e62e810d40decadcba",
            (1, False): "abcd0800c602040000100000ffffdcba",
            (1, True): "abcd0800d06e10e62e810d40decadcba",
            (2, False): "abcd0800c6020400001000005392dcba",
            (2, True): "abcd0800c6020400001000005392dcba",
        }
        for (transport, obfuscated), expected in fixtures.items():
            with self.subTest(transport=transport, xor=obfuscated):
                wire = protocol.encode_frame(self.ack, direction="response", transport=transport,
                                             obfuscated=obfuscated)
                self.assertEqual(wire.hex(), expected)
                decoded = protocol.decode_frame(wire, direction="response", transport=transport,
                                                obfuscated=obfuscated)
                self.assertEqual(decoded.message.encode(), self.ack)
                self.assertEqual(decoded.crc_verified, transport == 2)

    def test_request_roundtrip_all_transports_and_xor(self):
        body = protocol.build_read(0x1000, 128, 0x12345678)
        for transport in range(3):
            for obfuscated in (False, True):
                with self.subTest(transport=transport, xor=obfuscated):
                    wire = protocol.encode_frame(body, transport=transport, obfuscated=obfuscated)
                    decoded = protocol.decode_frame(wire, transport=transport, obfuscated=obfuscated)
                    self.assertEqual(decoded.message.encode(), body)
                    self.assertTrue(decoded.crc_verified)

    def test_xor_key_restarts_for_each_body(self):
        data = bytes(range(34))
        mixed = protocol.xor_payload(data)
        self.assertEqual(protocol.xor_payload(mixed), data)
        self.assertEqual(mixed[0] ^ data[0], mixed[16] ^ data[16])
        self.assertEqual(mixed[0] ^ data[0], mixed[32] ^ data[32])

    def test_response_placeholder_does_not_provide_integrity(self):
        wire = bytearray(protocol.encode_frame(self.ack, direction="response"))
        wire[8] ^= 1  # Change the address; the 0xffff placeholder is unchanged.
        decoded = protocol.decode_frame(bytes(wire), direction="response")
        self.assertEqual(decoded.checksum_status, "placeholder-unverified")
        self.assertFalse(decoded.crc_verified)

    def test_request_corruption_is_rejected(self):
        wire = protocol.encode_frame(protocol.build_read(0x1000, 16, 4))
        for offset in (0, 2, 4, 8, len(wire) - 4, len(wire) - 1):
            with self.subTest(offset=offset):
                damaged = bytearray(wire)
                damaged[offset] ^= 1
                with self.assertRaises(protocol.ProtocolError):
                    protocol.decode_frame(bytes(damaged))

    def test_no_automatic_direction_guessing(self):
        wire = protocol.encode_frame(self.ack, direction="response")
        with self.assertRaisesRegex(protocol.ProtocolError, "CRC16/X25"):
            protocol.decode_frame(wire, direction="request")

    def test_outer_and_inner_lengths_checked_independently(self):
        body = protocol.build_read(0x1000, 16, 4)
        with self.assertRaisesRegex(protocol.ProtocolError, "inner payload"):
            protocol.encode_frame(body[:2] + b"\x00\x00" + body[4:])
        wire = protocol.encode_frame(body)
        with self.assertRaisesRegex(protocol.ProtocolError, "outer body"):
            protocol.decode_frame(wire[:2] + b"\x11\x00" + wire[4:])

    def test_no_trailing_second_frame_accepted(self):
        wire = protocol.encode_frame(self.ack)
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode_frame(wire + wire)

    def test_transport_and_direction_validation(self):
        for options in ({"transport": 3}, {"direction": "guess"}):
            with self.subTest(options=options), self.assertRaises(protocol.ProtocolError):
                protocol.encode_frame(self.ack, **options)

    def test_encode_ring_capacity_constraint(self):
        huge = protocol.Message(0x1234, bytes(188)).encode()
        with self.assertRaisesRegex(protocol.ProtocolError, "receive ring"):
            protocol.encode_frame(huge, transport=2)


class BuilderTests(unittest.TestCase):
    def test_exact_read_layout(self):
        body = protocol.build_read(0x12340, 64, 0x12345678, reserved=0x9876)
        self.assertEqual(body.hex(), "c3020c00402301004000769878563412")

    def test_exact_write_layout(self):
        body = protocol.build_write(0x12340, b"ABC", 0x12345678, flag=1, reserved=2)
        self.assertEqual(body[:19].hex(), "c5020c00402301000300010278563412414243")
        self.assertEqual(body[19:], bytes(125))
        message = protocol.parse_message(body)
        self.assertEqual(message.declared_length, 12)
        self.assertEqual(message.actual_payload_length, 140)
        self.assertEqual(message.length_convention, "cps-0.56-fixed-128-byte-data-array")

    def test_compact_experiment_write_is_explicit(self):
        body = protocol.build_write(0x12340, b"ABC", 0x12345678, flag=1, reserved=2, layout="compact")
        self.assertEqual(body.hex(), "c5020f00402301000300010278563412414243")

    def test_cps_fixed_write_roundtrip_64_and_128(self):
        for count in (64, 128):
            with self.subTest(count=count):
                body = protocol.build_write(0x1000, bytes(range(count)), 7)
                frame = protocol.encode_frame(body, obfuscated=True)
                parsed = protocol.decode_frame(frame, obfuscated=True)
                self.assertEqual(len(body), 144)
                self.assertEqual(parsed.message.declared_length, 12)
                self.assertEqual(parsed.message.encode(), body)

    def test_known_cps_hello_legacy_length(self):
        body = protocol.Message(protocol.HELLO, struct.pack("<II", 7, 0) + bytes(16), 4).encode()
        parsed = protocol.decode_frame(protocol.encode_frame(body))
        self.assertEqual(parsed.message.declared_length, 4)
        self.assertEqual(parsed.message.actual_payload_length, 24)

    def test_cps_count_cannot_exceed_padded_data(self):
        body = bytearray(protocol.build_write(0x1000, bytes(64), 7))
        struct.pack_into("<H", body, 8, 129)
        with self.assertRaisesRegex(protocol.ProtocolError, "count does not match"):
            protocol.encode_frame(bytes(body))

    def test_count_and_complete_interval_constraints(self):
        for address, count in ((0, 0), (0, 129), (-1, 1), (0x1FF000, 1), (0x1FEFFF, 2)):
            with self.subTest(address=address, count=count), self.assertRaises(protocol.ProtocolError):
                protocol.build_read(address, count, 1)
        self.assertEqual(len(protocol.build_read(0x1FEF80, 128, 1)), 16)

    def test_unsigned_fields(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.build_read(0, 1, -1)
        with self.assertRaises(protocol.ProtocolError):
            protocol.build_write(0, b"x", 0, flag=256)


class DispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = protocol.ProgrammingModel(bytes(range(256)) * 8192)

    def setUp(self):
        self.state = protocol.ProgrammingState(0x12345678)

    def test_read_response_layout_and_data(self):
        result = self.model.dispatch(protocol.build_read(0x1040, 16, self.state.session), self.state)
        message = protocol.parse_message(result.response)
        self.assertEqual(message.command, protocol.READ_REPLY)
        self.assertEqual(message.payload[:8], struct.pack("<IHH", 0x1040, 16, 0))
        self.assertEqual(message.payload[8:], bytes(range(64, 80)))
        self.assertEqual(result.operation.operation, "read")

    def test_session_mismatch_silently_rejected(self):
        for body in (protocol.build_read(0, 16, 0), protocol.build_write(0, b"abc", 0)):
            with self.subTest(command=body[:2]):
                result = self.model.dispatch(body, self.state)
                self.assertIsNone(result.response)
                self.assertIsNone(result.operation)
                self.assertEqual(result.reason, "session-mismatch")

    def test_access_gate_zero_read_and_ack_without_write(self):
        state = protocol.ProgrammingState(self.state.session, access_state=1, secondary_state=1)
        result = self.model.dispatch(protocol.build_read(0x1040, 16, state.session), state)
        self.assertEqual(result.response[12:], bytes(16))
        self.assertIsNone(result.operation)
        result = self.model.dispatch(protocol.build_write(0x1040, b"abc", state.session), state)
        self.assertEqual(result.response.hex(), "c602040040100000")
        self.assertIsNone(result.operation)
        self.assertEqual(result.reason, "write-gated-ack")

    def test_non_one_access_state_still_gates(self):
        state = protocol.ProgrammingState(self.state.session, access_state=2)
        result = self.model.dispatch(protocol.build_write(0, b"abc", state.session), state)
        self.assertIsNone(result.operation)

    def test_special_17000_flag(self):
        state = protocol.ProgrammingState(self.state.session, protect_17000=1)
        for flag, writes in ((0, False), (1, True), (2, False)):
            with self.subTest(flag=flag):
                result = self.model.dispatch(protocol.build_write(0x17000, b"abc", state.session, flag=flag), state)
                self.assertEqual(result.operation is not None, writes)
                self.assertIsNotNone(result.response)

    def test_special_18000_secondary_gate_and_reload(self):
        state = protocol.ProgrammingState(self.state.session, secondary_state=1)
        body = protocol.build_write(0x18000, b"abc", state.session)
        blocked = self.model.dispatch(body, state)
        self.assertIsNone(blocked.operation)
        self.assertFalse(blocked.reload_special_area)
        allowed = self.model.dispatch(body, self.state)
        self.assertIsNotNone(allowed.operation)
        self.assertTrue(allowed.reload_special_area)

    def test_write_plan_keeps_mode_and_never_changes_snapshot(self):
        for address, mode in ((0x1000, 0), (0x4FFFF, 0), (0x50000, 1)):
            with self.subTest(address=address):
                before = self.model.snapshot[address:address + 3]
                result = self.model.dispatch(protocol.build_write(address, b"abc", self.state.session), self.state)
                self.assertEqual(result.operation.update_mode, mode)
                self.assertEqual(result.operation.data, b"abc")
                self.assertEqual(self.model.snapshot[address:address + 3], before)

    def test_stock_start_only_check_is_separate_from_strict_builder(self):
        body = protocol.Message(protocol.READ, struct.pack("<IHHI", 0x1FEFFF, 16, 0, self.state.session)).encode()
        result = self.model.dispatch(body, self.state)
        self.assertEqual(result.operation.count, 16)
        body = protocol.Message(protocol.READ, struct.pack("<IHHI", 0x1FF000, 16, 0, self.state.session)).encode()
        self.assertIsNone(self.model.dispatch(body, self.state).response)

    def test_stock_write_129_has_no_local_read_like_count_limit(self):
        data = bytes(range(129))
        payload = struct.pack("<IHHI", 0x1000, len(data), 0, self.state.session) + data
        result = self.model.dispatch(protocol.Message(protocol.WRITE, payload).encode(), self.state)
        self.assertEqual(result.operation.count, 129)
        read = protocol.Message(protocol.READ, payload[:12]).encode()
        self.assertIsNone(self.model.dispatch(read, self.state).response)

    def test_model_rejects_count_data_mismatch(self):
        payload = struct.pack("<IHHI", 0x1000, 64, 0, self.state.session) + b"abc"
        with self.assertRaisesRegex(protocol.ProtocolError, "count does not match"):
            self.model.dispatch(protocol.Message(protocol.WRITE, payload).encode(), self.state)

    def test_model_snapshot_bounds_are_explicit(self):
        tiny = protocol.ProgrammingModel(bytes(16))
        with self.assertRaisesRegex(protocol.ProtocolError, "supplied snapshot"):
            tiny.dispatch(protocol.build_read(8, 16, self.state.session), self.state)


class OptionalReferenceTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("K10_REFERENCE_RAW"), "optional decoded official sample not supplied")
    def test_stock_parser_dispatch_and_synthetic_nor_roundtrip(self):
        # Import here so normal CI needs only the standard library and does not
        # require Unicorn or any proprietary input file.
        from emulate_programming import run_experiments

        report = run_experiments(Path(os.environ["K10_REFERENCE_RAW"]).read_bytes())
        self.assertEqual(report["case_counts"], {"crc": 1, "parser": 8, "parser_dispatch": 27, "sender": 6})
        self.assertTrue(report["all_assertions_passed"])
        roundtrip = report["synthetic_nor_roundtrip"]
        self.assertEqual(roundtrip["passed_transactions"], 6)
        self.assertTrue(roundtrip["all_other_bytes_preserved"])
        self.assertTrue(roundtrip["gated_ack_without_storage_change_verified"])


if __name__ == "__main__":
    unittest.main()
