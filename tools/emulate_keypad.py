#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Execute pinned K10 Pro GPIO initialization, key scan and event filtering.

The firmware is supplied by the researcher, never bundled. This is a logical
contact model, not an electrical model or a full-radio emulator. Original GPIO
initialization and scan instructions execute; clock enabling, SysTick delay and
the final application event dispatcher are replaced at explicit boundaries.
"""

import argparse
import json
from pathlib import Path
import struct
import sys

from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_PROT_READ, UC_PROT_WRITE
from emulate_stock import EmulationError, StockEmulator

GPIO = {"A": 0x50000000, "B": 0x50000400,
        "C": 0x50000800, "F": 0x50001400}
ROWS = (("F", 8), ("B", 4), ("B", 3), ("A", 14))
COLUMNS = (("B", 15), ("B", 14), ("B", 13), ("B", 12), ("A", 8))
MATRIX = ((17, 1, 4, 7, 10), (12, 2, 5, 8, 11),
          (13, 3, 6, 9, None), (18, 14, 0, 15, 16))
DIRECT = {24: ("B", 15), 23: ("B", 14), 25: ("B", 13)}
INIT, SCAN, POLL = 0x08018B94, 0x08018C54, 0x08008A14
EVENT_DISPATCH = 0x080082FC
OVERRIDE, LAST_RAW, ACTIVE, COUNTER = 0x2000000A, 0x200000D2, 0x200000D3, 0x200000D0
HELD, PTT_STATE, PTT_COUNTER = 0x20000200, 0x20000201, 0x20000202
LONG_KEYS = frozenset((*range(10), 12, 13, 14, 15, 17, 23, 24, 25))


def pin_name(pin):
    return f"P{pin[0]}{pin[1]}"


class KeypadGpioModel:
    """Logical closure between one output row and an input column, or ground.

    All modeled registers start at synthetic zero. The real stock initializer
    supplies the subsequently checked modes, pulls, speeds and output types.
    Unused input bits are zero; only the five columns and PC13 have an explicit
    external-input model. Multiple rows on one closed column are rejected:
    push-pull drive contention and board isolation components are not modeled.
    """

    READ_OFFSETS = (0, 4, 8, 12, 16, 20, 32, 36)
    WRITE_OFFSETS = (0, 4, 8, 12, 20, 24, 32, 36, 40)

    def __init__(self, em):
        self.em = em
        self.registers = {port: {offset: 0 for offset in self.READ_OFFSETS}
                          for port in GPIO}
        self.contacts = set()
        self.direct = set()
        self.ptt = False
        self.trace = []
        em.uc.mem_map(0x50000000, 0x2000, UC_PROT_READ | UC_PROT_WRITE)
        em.uc.hook_add(UC_HOOK_MEM_READ, self._read, begin=0x50000000, end=0x50001FFF)
        em.uc.hook_add(UC_HOOK_MEM_WRITE, self._write, begin=0x50000000, end=0x50001FFF)

    def set_pressed(self, keys=(), *, ptt=False, contacts=()):
        keys = set(keys)
        valid = set(range(19)) | set(DIRECT)
        if not keys <= valid:
            raise ValueError(f"not scanner keys: {sorted(keys - valid)}")
        selected = {(r, c) for r, row in enumerate(MATRIX)
                    for c, key in enumerate(row) if key in keys}
        selected.update(contacts)
        if any(not (0 <= r < 4 and 0 <= c < 5) for r, c in selected):
            raise ValueError("contact is outside the four-by-five geometry")
        for column in range(5):
            if sum(c == column for _, c in selected) > 1:
                raise ValueError("different closed rows on one column need an electrical model")
            if any(c == column for _, c in selected) and COLUMNS[column] in (
                    DIRECT[key] for key in keys & set(DIRECT)):
                raise ValueError("a direct switch and matrix row share a closed column")
        self.contacts = selected
        self.direct = keys & set(DIRECT)
        self.ptt = bool(ptt)

    def output_high(self, pin):
        port, bit = pin
        return bool(self.registers[port][20] & (1 << bit))

    def pin_config(self, pin):
        port, bit = pin
        registers = self.registers[port]
        return {"mode": (registers[0] >> (bit * 2)) & 3,
                "output_type": (registers[4] >> bit) & 1,
                "speed": (registers[8] >> (bit * 2)) & 3,
                "pull": (registers[12] >> (bit * 2)) & 3,
                "output_latch": int(self.output_high(pin))}

    def input_value(self, port):
        value = 0
        for pin in (*COLUMNS, ("C", 13)):
            if pin[0] != port:
                continue
            config = self.pin_config(pin)
            if config["mode"] != 0 or config["pull"] != 1:
                raise EmulationError(f"input {pin_name(pin)} lacks expected input/pull-up setup")
            value |= 1 << pin[1]
        for row, column in self.contacts:
            row_pin, column_pin = ROWS[row], COLUMNS[column]
            if self.pin_config(row_pin)["mode"] != 1:
                raise EmulationError(f"row {pin_name(row_pin)} is not an output")
            if column_pin[0] == port and not self.output_high(row_pin):
                value &= ~(1 << column_pin[1])
        for code in self.direct:
            direct_pin = DIRECT[code]
            if direct_pin[0] == port:
                value &= ~(1 << direct_pin[1])
        if port == "C" and self.ptt:
            value &= ~(1 << 13)
        return value

    def _decode(self, address, size, allowed):
        for port, base in GPIO.items():
            offset = address - base
            if size == 4 and offset in allowed:
                return port, offset
        raise EmulationError(f"unmodeled GPIO access 0x{address:08x}/{size}")

    def _abort(self, error):
        self.em._fault = f"keypad GPIO: {error}"
        self.em.uc.emu_stop()

    def _read(self, uc, access, address, size, value, user_data):
        del uc, access, value, user_data
        try:
            port, offset = self._decode(address, size, self.READ_OFFSETS)
            value = self.input_value(port) if offset == 16 else self.registers[port][offset]
            self.em.put_u32(address, value)
            self.trace.append({"op": "read", "port": port, "offset": offset, "value": value})
        except EmulationError as error:
            self._abort(error)

    def _write(self, uc, access, address, size, value, user_data):
        del uc, access, user_data
        try:
            port, offset = self._decode(address, size, self.WRITE_OFFSETS)
            registers = self.registers[port]
            if offset == 24:  # BSRR
                registers[20] = (registers[20] & ~(value >> 16)) | (value & 0xFFFF)
            elif offset == 40:  # BRR
                registers[20] &= ~(value & 0xFFFF)
            else:
                registers[offset] = value
            self.trace.append({"op": "write", "port": port, "offset": offset, "value": value})
        except EmulationError as error:
            self._abort(error)


class KeypadExperiment:
    def __init__(self, raw):
        self.em = StockEmulator(raw)
        self.em.initialize_data()
        self.gpio = KeypadGpioModel(self.em)
        self.events = []
        self.poll_index = 0
        self.clock_masks = []
        self.delay_arguments = []
        self.em.hook_function(0x0801B168, self._clock, name="gpio_clock_enable_boundary")
        self.em.hook_function(0x0801D010, self._delay, name="systick_delay_boundary")
        self.em.hook_function(EVENT_DISPATCH, self._event, name="application_event_capture")
        self.init_result = self.em.call(INIT)
        self.initial_pins = {pin_name(pin): self.gpio.pin_config(pin)
                             for pin in (*ROWS, *COLUMNS, ("C", 13))}
        self.reset_filter()

    def _clock(self, em):
        self.clock_masks.append(em.reg(0))

    def _delay(self, em):
        self.delay_arguments.append(em.reg(0))

    def _event(self, em):
        self.events.append({"poll": self.poll_index, "key": em.reg(0),
                            "pressed": em.reg(1), "held": em.reg(2)})

    def reset_filter(self, *, app_state=0):
        for address, data in ((OVERRIDE, b"\xff"), (LAST_RAW, b"\xff"),
                              (ACTIVE, b"\xff"), (COUNTER, b"\0\0"),
                              (HELD, b"\0\0\0"), (0x20000198, bytes([app_state])),
                              (0x20000163, b"\0"), (0x2000250A, b"\0"),
                              (0x20000281, b"\0"), (0x200000D5, b"\0")):
            self.em.write(address, data)
        self.gpio.set_pressed()
        self.events.clear()
        self.gpio.trace.clear()
        self.delay_arguments.clear()
        self.poll_index = 0

    def scan(self, keys=(), **kwargs):
        self.gpio.set_pressed(keys, **kwargs)
        self.gpio.trace.clear()
        self.delay_arguments.clear()
        result = self.em.call(SCAN)
        if not all(self.gpio.output_high(pin) for pin in ROWS):
            raise EmulationError("scan did not restore all row outputs high")
        return result

    def polls(self, count, keys=(), *, ptt=False):
        self.gpio.set_pressed(keys, ptt=ptt)
        for _ in range(count):
            self.poll_index += 1
            self.em.call(POLL)
        return list(self.events)

    def snapshot(self):
        return {"last_raw": self.em.read(LAST_RAW, 1)[0],
                "active": self.em.read(ACTIVE, 1)[0],
                "counter": struct.unpack("<H", self.em.read(COUNTER, 2))[0],
                "held": self.em.read(HELD, 1)[0],
                "ptt": self.em.read(PTT_STATE, 1)[0],
                "ptt_counter": self.em.read(PTT_COUNTER, 1)[0]}


def run_ui_semantics(raw):
    """Check selected UI dataflow without whole-application or RF execution.

    Call actual main/menu mode handlers. Their common global dispatcher is
    intentionally outside this experiment. RAM is a documented synthetic
    fixture; only the A/B case replaces a downstream channel-refresh helper.
    """
    cases = []

    def fixture():
        em = StockEmulator(raw)
        em.initialize_data()
        for address in (0x200000C6, 0x200000FA, 0x20000095, 0x200000E9,
                        0x20000106, 0x20000107, 0x2000010A, 0x200000B8):
            em.write(address, b"\0")
        em.write(0x200000D9, b"\xff")
        em.write(0x20000198, b"\x02")
        em.write(0x20000108, b"\x02")
        em.write(0x20000218, b"\x0c")
        em.put_u32(0x20000184, 0x21000000)
        return em

    em = fixture()
    conversions = [em.call(0x08006EF4, (0, key)).r0 for key in range(16)]
    if conversions[:10] != list(range(0x30, 0x3A)) or conversions[14:16] != [0x2A, 0x23]:
        raise EmulationError("digit/star/hash character mapping differs")
    cases.append({"name": "signaling_profile0_character_mapping", "function": "0x08006ef4",
                  "keys": list(range(16)), "characters": "".join(map(chr, conversions)),
                  "passed": True})
    em = fixture()
    em.write(0x20000106, b"\x03")
    em.call(0x08009CB4, (17, 1, 0))
    actual = [em.read(a, 1)[0] for a in (0x20000106, 0x200000D9)]
    if actual != [0, 2]:
        raise EmulationError("MENU did not request menu state 2 at top level")
    cases.append({"name": "menu_from_main", "key": 17, "handler": "0x08009cb4",
                  "menu_depth": actual[0], "requested_ui_state": actual[1], "passed": True})
    for name, key, depth, expected_depth, expected_state in (
            ("menu_enter_sublevel", 17, 0, 1, 2),
            ("exit_to_parent", 18, 1, 0, 2),
            ("exit_to_main", 18, 0, 0, 0)):
        em = fixture()
        em.write(0x20000106, bytes([depth]))
        em.call(0x0800A884, (key, 1, 0))
        actual = [em.read(a, 1)[0] for a in (0x20000106, 0x200000D9)]
        if actual != [expected_depth, expected_state]:
            raise EmulationError(f"{name}: unexpected menu depth/state {actual}")
        cases.append({"name": name, "key": key, "handler": "0x0800a884",
                      "initial_menu_depth": depth, "menu_depth": actual[0],
                      "requested_ui_state": actual[1], "passed": True})
    for key, expected in ((12, 1), (13, 3)):
        em = fixture()
        em.call(0x0800A884, (key, 1, 0))
        index = em.read(0x20000108, 1)[0]
        if index != expected:
            raise EmulationError("menu direction/index differs")
        cases.append({"name": "menu_previous" if key == 12 else "menu_next", "key": key,
                      "handler": "0x0800a884", "initial_index": 2, "index": index, "passed": True})
    for initial in (0, 1):
        em = fixture()
        em.write(0x20000198, b"\0")
        em.write(0x200023E4, bytes([initial]))
        em.call(0x08009CB4, (15, 1, 1))
        lock = em.read(0x200023E4, 1)[0]
        if lock != 1 - initial:
            raise EmulationError("long key15 did not toggle lock field")
        cases.append({"name": "long_function_lock_toggle", "key": 15,
                      "handler": "0x08009cb4", "initial_lock": initial,
                      "lock": lock, "ram_field": "0x200023e4", "passed": True})
    for initial in (0, 1):
        em = fixture()
        em.write(0x200023EC, b"\0")
        em.write(0x200023E0, bytes([initial]))
        em.hook_function(0x08006B9C, lambda machine: None, name="channel_refresh_boundary")
        em.call(0x08009CB4, (10, 1, 0))
        selected = em.read(0x200023E0, 1)[0]
        if selected != 1 - initial:
            raise EmulationError("key10 did not toggle selected A/B field")
        cases.append({"name": "ab_selection_toggle", "key": 10, "handler": "0x08009cb4",
                      "initial_selection": initial, "selection": selected,
                      "ram_field": "0x200023e0", "boundary": "0x08006b9c", "passed": True})
    return cases


def run_timing(raw):
    """Execute timer setup/delays against an explicit synthetic SysTick counter.

    A 72 MHz clock is an experiment input. Advancing VAL by 72 counts on each
    read makes the requested delay testable, without simulating CPU cycles,
    interrupts, scheduler jitter or real-time accuracy.
    """
    em = StockEmulator(raw)
    em.initialize_data()
    em.put_u32(0x20000000, 72_000_000)
    registers = {0xE000E010: 0, 0xE000E014: 0, 0xE000E018: 0, 0xE000ED20: 0}
    reads = []
    em.uc.mem_map(0xE000E000, 0x1000, UC_PROT_READ | UC_PROT_WRITE)

    def access(uc, access_type, address, size, value, user_data):
        del access_type, user_data
        if size != 4 or address not in registers:
            em._fault = f"unmodeled synthetic SysTick access 0x{address:08x}/{size}"
            uc.emu_stop()
            return
        if value is not None:
            registers[address] = value
        else:
            em.put_u32(address, registers[address])
            if address == 0xE000E018:
                reads.append(registers[address])
                registers[address] = (registers[address] - 72) % (registers[0xE000E014] + 1)

    em.uc.hook_add(UC_HOOK_MEM_READ,
                  lambda u, a, p, s, v, d: access(u, a, p, s, None, d),
                  begin=0xE000E000, end=0xE000EFFF)
    em.uc.hook_add(UC_HOOK_MEM_WRITE, access, begin=0xE000E000, end=0xE000EFFF)
    em.call(0x0801A520)
    reload = registers[0xE000E014]
    scale = em.u32(0x2000003C)
    if (reload, scale, registers[0xE000E010]) != (719999, 72, 7):
        raise EmulationError("SysTick setup does not match clock/100 and clock/1e6")
    cases = []
    for address, name, expected in ((0x0801D010, "delay_1us_argument", 72),
                                     (0x0801D000, "delay_1ms_wrapper", 72000)):
        registers[0xE000E018] = reload
        reads.clear()
        result = em.call(address, (1,))
        elapsed = reads[0] - reads[-1]
        if elapsed != expected:
            raise EmulationError(f"{name}: unexpected synthetic timer delta {elapsed}")
        cases.append({"name": name, "function": f"0x{address:08x}", "argument": 1,
                      "synthetic_timer_delta": elapsed, "value_reads": len(reads),
                      "instructions": result.instructions, "passed": True})
    return {"initializer": "0x0801a520", "synthetic_core_hz": 72_000_000,
            "reload": reload, "control": registers[0xE000E010], "ticks_per_us": scale,
            "nominal_systick_hz": 100, "systick_vector": "0x0801ba71",
            "poll_route": ["0x0801ba70", "flag 0x20000014", "0x0801d2e6",
                           "0x0800c628", "0x0800c9ba", "0x08008a14"],
            "limitation": "Synthetic timer reads verify units only. Main-loop flag coalescing and blocking work can lengthen polling intervals.",
            "cases": cases}


def run_keypad(raw):
    exp = KeypadExperiment(raw)
    expected_row = {"mode": 1, "output_type": 0, "speed": 3, "pull": 1, "output_latch": 1}
    for pin in ROWS:
        if exp.initial_pins[pin_name(pin)] != expected_row:
            raise EmulationError(f"unexpected row setup: {pin_name(pin)}")
    for pin in (*COLUMNS, ("C", 13)):
        if exp.initial_pins[pin_name(pin)]["mode"] != 0 or exp.initial_pins[pin_name(pin)]["pull"] != 1:
            raise EmulationError(f"unexpected input setup: {pin_name(pin)}")
    if exp.clock_masks != [1, 2, 4, 32]:
        raise EmulationError("unexpected GPIO clock enable masks")
    scans = []
    cases = [("idle", (), 255, {})]
    cases.extend((f"single_{key}", (key,), key, {}) for key in (*range(19), 24, 23, 25))
    cases.extend([
        ("same_row_priority", (1, 4), 1, {}),
        ("different_rows_distinct_columns", (4, 2), 4, {}),
        ("direct_over_matrix", (24, 1), 24, {}),
        ("direct_priority", (23, 24, 25), 24, {}),
        ("unscanned_pb3_pa8", (), 255, {"contacts": ((2, 4),)}),
        ("ptt_absent_from_raw_scan", (), 255, {"ptt": True}),
    ])
    for name, keys, expected, kwargs in cases:
        result = exp.scan(keys, **kwargs)
        if result.r0 != expected:
            raise EmulationError(f"{name}: expected {expected}, got {result.r0}")
        row_low_order = [pin_name(pin) for item in exp.gpio.trace if item["op"] == "write" and item["offset"] == 40
                         for pin in ROWS if pin[0] == item["port"] and item["value"] & (1 << pin[1])]
        scans.append({"name": name, "pressed_keys": list(keys), "result": result.r0,
                      "row_low_order": row_low_order, "delay_arguments": list(exp.delay_arguments),
                      "gpio_reads": sum(t["op"] == "read" for t in exp.gpio.trace),
                      "instructions": result.instructions, "passed": True})
    exp.em.write(OVERRIDE, b"\x07")
    result = exp.scan((1,))
    if result.r0 != 7 or exp.gpio.trace:
        raise EmulationError("override did not bypass GPIO scan")
    scans.append({"name": "software_override", "result": result.r0,
                  "gpio_reads": 0, "instructions": result.instructions, "passed": True})
    filtering = []

    def scenario(name, phases, expected_events, *, app_state=0):
        exp.reset_filter(app_state=app_state)
        for count, keys, ptt in phases:
            exp.polls(count, keys, ptt=ptt)
        actual = [(e["poll"], e["key"], e["pressed"], e["held"]) for e in exp.events]
        if actual != expected_events:
            raise EmulationError(f"{name}: event mismatch {actual}, expected {expected_events}")
        filtering.append({"name": name, "app_state": app_state,
                          "phases": [{"polls": n, "keys": list(k), "ptt": p} for n, k, p in phases],
                          "events": list(exp.events), "final_state": exp.snapshot(), "passed": True})

    scenario("short_press_release", [(5, (1,), False), (5, (), False)], [(5, 1, 1, 0), (10, 1, 0, 0)])
    scenario("four_sample_glitch", [(4, (1,), False), (5, (), False)], [])
    scenario("stable_key_transition", [(5, (1,), False), (5, (2,), False)],
             [(5, 1, 1, 0), (10, 1, 0, 0), (10, 2, 1, 0)])
    scenario("long_digit_release", [(129, (1,), False), (5, (), False)],
             [(5, 1, 1, 0), (129, 1, 1, 1), (134, 1, 0, 1)])
    scenario("repeat_direction_key", [(161, (12,), False), (5, (), False)],
             [(5, 12, 1, 0), (129, 12, 1, 1), (145, 12, 1, 1), (161, 12, 1, 1), (166, 12, 0, 1)])
    scenario("no_long_for_code18", [(161, (18,), False), (5, (), False)],
             [(5, 18, 1, 0), (166, 18, 0, 0)])
    scenario("state1_repeat_digit5", [(161, (5,), False)],
             [(5, 5, 1, 0), (129, 5, 1, 1), (145, 5, 1, 1), (161, 5, 1, 1)], app_state=1)
    scenario("ptt_press5_release2", [(5, (), True), (2, (), False)],
             [(5, 21, 1, 0), (7, 21, 0, 0)])
    scenario("ptt_glitches", [(4, (), True), (2, (), False), (5, (), True),
                              (1, (), False), (2, (), True), (2, (), False)],
             [(11, 21, 1, 0), (16, 21, 0, 0)])
    scenario("ptt_and_matrix", [(5, (2,), True), (2, (2,), False), (5, (), False)],
             [(5, 21, 1, 0), (5, 2, 1, 0), (7, 21, 0, 0), (12, 2, 0, 0)])
    ui = run_ui_semantics(raw)
    timing = run_timing(raw)

    return {"sample_sha256": exp.em.sample_sha256,
            "functions": {"gpio_init": f"0x{INIT:08x}", "raw_scan": f"0x{SCAN:08x}",
                          "poll_filter": f"0x{POLL:08x}", "event_capture": f"0x{EVENT_DISPATCH:08x}"},
            "model": "Logical contacts; stock initialization, GPIO helper, scanner and filter execute. Clock enable, SysTick delay and application dispatch are explicit hooks.",
            "limitations": "No whole-system time, real PCB continuity, voltage, diode, ghosting, output contention or RF behavior is simulated. Rows/columns are MCU port bits, not package pins or connector pads.",
            "initial_conditions": "Scatter data executed; BSS and modeled GPIO registers initially zero; application state and input flags explicitly reset for each filter case.",
            "gpio_clock_masks": exp.clock_masks, "initial_pins": exp.initial_pins,
            "rows": [pin_name(p) for p in ROWS], "columns": [pin_name(p) for p in COLUMNS],
            "matrix_codes": MATRIX,
            "direct_codes": {str(k): pin_name(v) for k, v in DIRECT.items()},
            "ptt": {"pin": "PC13", "active_low": True, "event_code": 21,
                    "press_polls": 5, "release_polls": 2},
            "scan_cases": scans, "filter_cases": filtering, "ui_cases": ui, "timing": timing,
            "passed": len(scans) + len(filtering) + len(ui) + len(timing["cases"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        rendered = json.dumps(run_keypad(args.raw.read_bytes()), indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    except (OSError, ValueError, EmulationError) as error:
        print(f"emulate_keypad: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
