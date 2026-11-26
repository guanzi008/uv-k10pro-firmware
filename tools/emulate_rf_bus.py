#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Replay stock RF-register bit-banging with an explicit GPIO register model.

This records logical edges. It does not emulate the RF IC, analog behavior,
clock accuracy, antenna, GPIO electrical characteristics or physical timing.
"""

import argparse
import json
from pathlib import Path
import sys

from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_PROT_READ, UC_PROT_WRITE
from emulate_stock import EmulationError, StockEmulator

GPIO_BASE = 0x50001400
CS, CLOCK, DATA = 1 << 9, 1 << 5, 1 << 6
WRITE_FUNCTION, READ_FUNCTION = 0x08018944, 0x08018248


def bits_to_hex(bits):
    if len(bits) % 8:
        raise ValueError("bit count is not a whole byte")
    return bytes(sum(bit << (7 - j) for j, bit in enumerate(bits[i:i + 8]))
                 for i in range(0, len(bits), 8)).hex()


class GpioSerialModel:
    """Only MODER, IDR, BSRR and BRR at the observed GPIO base are modeled."""

    def __init__(self, emulator, read_word=None):
        self.emulator = emulator
        self.output = CS | CLOCK | DATA
        # Synthetic initial condition: all three control pins are outputs.
        # The stock read routine then switches only pin 6 to input and back.
        self.mode = sum(1 << (pin * 2) for pin in (5, 6, 9))
        self.input_bits = ([] if read_word is None else
                           [(read_word >> n) & 1 for n in range(15, -1, -1)])
        self.input_position = 0
        self.transactions = []
        self.current = None
        self.direction_changes = []
        self.writes = 0
        # Unicorn maps 4 KiB pages. Hooks reject access outside these 4 registers,
        # including other GPIO blocks sharing this page.
        emulator.uc.mem_map(0x50001000, 0x1000, UC_PROT_READ | UC_PROT_WRITE)
        emulator.put_u32(GPIO_BASE, self.mode)
        emulator.uc.hook_add(UC_HOOK_MEM_WRITE, self._write, begin=0x50001000, end=0x50001FFF)
        emulator.uc.hook_add(UC_HOOK_MEM_READ, self._read, begin=0x50001000, end=0x50001FFF)

    def _abort(self, message):
        self.emulator._fault = f"GPIO serial model: {message}"
        self.emulator.uc.emu_stop()

    def _write(self, uc, access, address, size, value, user_data):
        del uc, access, user_data
        offset = address - GPIO_BASE
        if size != 4 or offset not in (0, 0x18, 0x28):
            self._abort(f"unmodeled write 0x{address:08x}/{size}")
            return
        self.writes += 1
        if offset == 0:
            self.mode = value
            self.direction_changes.append((value >> 12) & 3)
            return
        previous = self.output
        if offset == 0x18:
            self.output = (self.output | (value & 0xFFFF)) & ~(value >> 16)
        else:
            self.output &= ~(value & 0xFFFF)
        if (previous ^ self.output) & ~(CS | CLOCK | DATA):
            self._abort("a different GPIO pin changed")
            return
        if previous & CS and not self.output & CS:
            if self.current is not None:
                self._abort("nested chip selection")
                return
            self.current = {"output_bits": [], "input_bits": []}
        if not previous & CLOCK and self.output & CLOCK and not self.output & CS:
            if self.current is None:
                self._abort("clock while unselected")
                return
            if ((self.mode >> 12) & 3) == 1:
                self.current["output_bits"].append(int(bool(self.output & DATA)))
        if not previous & CS and self.output & CS:
            if self.current is None:
                self._abort("chip deselection without transaction")
                return
            self.transactions.append(self.current)
            self.current = None

    def _read(self, uc, access, address, size, value, user_data):
        del uc, access, value, user_data
        offset = address - GPIO_BASE
        if size != 4 or offset not in (0, 0x10):
            self._abort(f"unmodeled read 0x{address:08x}/{size}")
            return
        if offset == 0:
            self.emulator.put_u32(address, self.mode)
            return
        if self.current is None or ((self.mode >> 12) & 3) != 0 or not self.output & CLOCK:
            self._abort("IDR read outside an input/high-clock transaction")
            return
        if self.input_position >= len(self.input_bits):
            self._abort("more input bits requested than supplied")
            return
        bit = self.input_bits[self.input_position]
        self.input_position += 1
        self.current["input_bits"].append(bit)
        self.emulator.put_u32(address, bit * DATA)


def run_rf_bus(raw: bytes) -> dict:
    cases = []
    for operation, register, value in [
        ("write", 0x38, 0x1234), ("write", 0, 0), ("write", 0x7F, 0xFFFF),
        ("read", 0x67, 0xA55A), ("read", 0, 0), ("read", 0x7F, 0xFFFF),
    ]:
        em = StockEmulator(raw)
        model = GpioSerialModel(em, read_word=value if operation == "read" else None)
        if operation == "write":
            result = em.call(WRITE_FUNCTION, (register, value))
            expected_output = bytes([register, value >> 8, value & 255]).hex()
            expected_input = ""
        else:
            result = em.call(READ_FUNCTION, (register,))
            expected_output = bytes([register | 0x80]).hex()
            expected_input = value.to_bytes(2, "big").hex()
            if result.r0 != value or model.direction_changes != [0, 1]:
                raise EmulationError("read value or input/output direction transition differs")
        if len(model.transactions) != 1 or model.current is not None:
            raise EmulationError("expected exactly one complete chip-select transaction")
        transaction = model.transactions[0]
        output_hex = bits_to_hex(transaction["output_bits"])
        input_hex = bits_to_hex(transaction["input_bits"])
        if output_hex != expected_output or input_hex != expected_input:
            raise EmulationError("observed serial bit sequence differs")
        if model.output != CS | CLOCK | DATA:
            raise EmulationError("unexpected idle pin state")
        cases.append({
            "operation": operation, "register": f"0x{register:02x}",
            "value": f"0x{value:04x}", "passed": True,
            "controller_output_hex": output_hex, "synthetic_input_hex": input_hex,
            "output_bits": len(transaction["output_bits"]),
            "input_bits": len(transaction["input_bits"]),
            "data_direction_modes": model.direction_changes,
            "gpio_writes": model.writes, "instructions": result.instructions,
        })
    # This helper splits a supplied 32-bit word across two RF registers. The
    # synthetic value verifies ordering; it is not a requested radio frequency.
    em = StockEmulator(raw)
    model = GpioSerialModel(em)
    result = em.call(0x08018480, (0x12345678,))
    words = [bits_to_hex(item["output_bits"]) for item in model.transactions]
    if words != ["385678", "391234"] or model.current is not None:
        raise EmulationError("32-bit register-pair output differs")
    cases.append({
        "operation": "write_register_pair", "function": "0x08018480",
        "synthetic_word": "0x12345678", "passed": True,
        "controller_output_hex": words, "gpio_writes": model.writes,
        "instructions": result.instructions,
    })
    return {
        "sample_sha256": StockEmulator(raw).sample_sha256,
        "write_function": f"0x{WRITE_FUNCTION:08x}",
        "read_function": f"0x{READ_FUNCTION:08x}",
        "gpio_base": f"0x{GPIO_BASE:08x}",
        "pins": {"chip_select_active_low": 9, "clock": 5, "bidirectional_data": 6},
        "executed": "Original register read/write, bit loops, direction helper and delay instructions; no function replacements.",
        "modeled": "GPIO MODER/IDR/BSRR/BRR semantics and explicit 16-bit synthetic input; all other MMIO rejected.",
        "initial_conditions": "Pins 5, 6 and 9 start as outputs with high output latches; the read routine controls pin 6 direction.",
        "conclusion": "MSB-first 8-bit register command plus 16-bit data; read command sets bit 7. Compatible with a BK48xx-style 3-wire register interface, not a unique chip identification.",
        "limitations": "Logical GPIO edges only: no real bus capture, pin voltage, signal timing, RF tuning, transmit or receiver emulation.",
        "cases": cases, "passed": len(cases),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = run_rf_bus(args.raw.read_bytes())
        rendered = json.dumps(report, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    except (OSError, ValueError, EmulationError) as error:
        print(f"emulate_rf_bus: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
