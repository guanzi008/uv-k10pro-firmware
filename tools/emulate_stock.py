#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Execute isolated functions from an explicitly supplied stock image.

This is a function-level CPU experiment, not a radio/bootloader emulator.
MMIO is deliberately unmapped. Every replaced hardware function is recorded;
flash, timers, IRQs, DMA, RF, and physical timing are not silently simulated.
No vendor code bytes are distributed with this module.
"""

import argparse
from collections import deque
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
import sys

from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_INVALID, UC_PROT_READ, UC_PROT_WRITE, UC_PROT_EXEC
from unicorn.arm_const import (
    UC_CPU_ARM_CORTEX_M0, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
    UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6,
    UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10,
    UC_ARM_REG_R11, UC_ARM_REG_R12, UC_ARM_REG_SP, UC_ARM_REG_LR,
    UC_ARM_REG_PC, UC_ARM_REG_XPSR,
)

APP_BASE = 0x08002000
FLASH_BASE = 0x08000000
FLASH_SIZE = 0x20000
RAM_BASE = 0x20000000
RAM_SIZE = 0x4000
SCRATCH_BASE = 0x21000000
SCRATCH_SIZE = 0x10000
RETURN_ADDRESS = 0x01000000
REFERENCE_SHA256 = "061759672cc69a1b00420ce6fae67b0756d3db111f8fa4329cadc940dcd17f17"
REGISTERS = (
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
    UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
    UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
    UC_ARM_REG_R12,
)


class EmulationError(RuntimeError):
    pass


@dataclass(frozen=True)
class CallResult:
    r0: int
    r1: int
    instructions: int
    hardware_hooks: int


class StockEmulator:
    """Cortex-M0 instruction model with explicit external-function boundaries.

    ``raw`` is the decoded application, not the XOR/CRC update container.
    Production experiments require the pinned reference digest. Synthetic unit
    tests may pass ``require_reference=False`` with independently made code.
    A separate artificial scratch page is provided for controlled input/output
    buffers; it is not a claim about memory fitted to the radio.
    """

    def __init__(self, raw: bytes, *, require_reference: bool = True):
        if not __debug__:
            raise EmulationError("instruction experiments require assertions enabled; do not use Python -O")
        digest = hashlib.sha256(raw).hexdigest()
        if require_reference and digest != REFERENCE_SHA256:
            raise EmulationError(f"unsupported raw sample SHA-256: {digest}")
        if not raw or len(raw) > FLASH_SIZE - (APP_BASE - FLASH_BASE):
            raise EmulationError("raw image does not fit the application window")
        self.sample_sha256 = digest
        self.reference_verified = require_reference
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self.uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M0)
        self.uc.mem_map(FLASH_BASE, FLASH_SIZE, UC_PROT_READ | UC_PROT_WRITE | UC_PROT_EXEC)
        self.uc.mem_write(FLASH_BASE, b"\xff" * FLASH_SIZE)
        self.uc.mem_write(APP_BASE, raw)
        self.uc.mem_protect(FLASH_BASE, FLASH_SIZE, UC_PROT_READ | UC_PROT_EXEC)
        self.uc.mem_map(RAM_BASE, RAM_SIZE, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.mem_map(SCRATCH_BASE, SCRATCH_SIZE, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.mem_map(RETURN_ADDRESS, 0x1000, UC_PROT_READ | UC_PROT_EXEC)
        self.hooks = {}
        self.events = []
        self.trace = deque(maxlen=32)
        self.instructions = 0
        self._fault = None
        self._returned = False
        self.uc.hook_add(UC_HOOK_CODE, self._on_code)
        self.uc.hook_add(UC_HOOK_MEM_INVALID, self._on_invalid_memory)

    def read(self, address: int, size: int) -> bytes:
        return bytes(self.uc.mem_read(address, size))

    def write(self, address: int, data: bytes) -> None:
        self.uc.mem_write(address, data)

    def u32(self, address: int) -> int:
        return struct.unpack("<I", self.read(address, 4))[0]

    def put_u32(self, address: int, value: int) -> None:
        self.write(address, struct.pack("<I", value))

    def reg(self, index: int) -> int:
        return self.uc.reg_read(REGISTERS[index])

    def set_reg(self, index: int, value: int) -> None:
        self.uc.reg_write(REGISTERS[index], value & 0xffffffff)

    def hook_function(self, address: int, callback, *, name: str) -> None:
        """Replace one known function, logging r0..r3 and returning through LR.

        ``callback(emulator)`` can inspect arguments and memory; returning an
        integer replaces r0. Returning None preserves r0. Callbacks must not
        recursively invoke ``call`` on the same engine.
        """
        self.hooks[address & ~1] = (name, callback)

    def _on_code(self, uc, address, size, user_data):
        del size, user_data
        self.instructions += 1
        self.trace.append(address)
        if address == RETURN_ADDRESS:
            self._returned = True
            uc.emu_stop()
            return
        if address in self.hooks:
            name, callback = self.hooks[address]
            self.events.append({"function": name, "pc": f"0x{address:08x}",
                                "args": [self.reg(i) for i in range(4)]})
            try:
                result = callback(self)
                if result is not None:
                    self.set_reg(0, result)
                uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
            except Exception as error:
                self._fault = f"hook {name}: {error}"
                uc.emu_stop()

    def _on_invalid_memory(self, uc, access, address, size, value, user_data):
        del uc, value, user_data
        self._fault = f"unmodeled/protected memory access={access} address=0x{address:08x} size={size}"
        return False

    def call(self, address: int, args=(), *, stack_args=(), limit=500000,
             stack_pointer=RAM_BASE + RAM_SIZE - 0x200) -> CallResult:
        if len(args) > 4:
            raise ValueError("pass at most four register args; use stack_args for the rest")
        if not 1 <= limit <= 10000000:
            raise ValueError("instruction limit must be 1..10000000")
        if stack_pointer & 7:
            raise ValueError("stack pointer must be 8-byte aligned")
        for register in REGISTERS:
            self.uc.reg_write(register, 0)
        for i, value in enumerate(args):
            self.set_reg(i, value)
        for i, value in enumerate(stack_args):
            self.put_u32(stack_pointer + i * 4, value)
        self.uc.reg_write(UC_ARM_REG_SP, stack_pointer)
        self.uc.reg_write(UC_ARM_REG_LR, RETURN_ADDRESS | 1)
        self.uc.reg_write(UC_ARM_REG_XPSR, 1 << 24)
        self.instructions = 0
        self.trace.clear()
        self._fault = None
        self._returned = False
        before = len(self.events)
        try:
            # A wall-clock bound is a host safeguard, not a target time model.
            self.uc.emu_start(address | 1, RETURN_ADDRESS + 2,
                              timeout=5000000, count=limit)
        except UcError as error:
            raise EmulationError(self._diagnostic(str(error))) from error
        if self._fault or not self._returned:
            raise EmulationError(self._diagnostic("function did not return within limits"))
        return CallResult(self.reg(0), self.reg(1), self.instructions, len(self.events) - before)

    def _diagnostic(self, message: str) -> str:
        return (f"{self._fault or message}; pc=0x{self.uc.reg_read(UC_ARM_REG_PC):08x}; "
                f"last PCs={[hex(pc) for pc in self.trace]}")

    def initialize_data(self) -> CallResult:
        """Run the reference scatter decompressor for its 1,220-byte data area.

        BSS is initially zero from the mapped SRAM. No startup/PLL/IRQ routine
        is executed. This initializer is only meaningful for the pinned image.
        """
        if not self.reference_verified:
            raise EmulationError("scatter initialization requires the reference image")
        source, destination, length, function = struct.unpack("<4I", self.read(0x0801F008, 16))
        if (source, destination, length, function) != (0x0801F028, 0x20000000, 0x4C4, 0x08002AB8):
            raise EmulationError("unexpected scatter descriptor")
        return self.call(function, (source, destination, length))


class NorModel:
    """Synthetic external NOR backing for the three observed low-level calls.

    Only actual stock functions above these hooks execute. Read, sector erase,
    and page program below them are replaced by a byte array with 1-to-0 NOR
    semantics. Capacity is an experiment input, not a hardware determination.
    """

    def __init__(self, size=0x200000):
        if size < 4096 or size % 4096:
            raise ValueError("synthetic NOR size must be a positive sector multiple")
        self.data = bytearray(b"\xff" * size)
        self.operations = []

    def _bounds(self, address, size):
        if address > len(self.data) or size > len(self.data) - address:
            raise EmulationError("synthetic NOR access out of range")

    def attach(self, emulator: StockEmulator):
        emulator.hook_function(0x080195E4, self._read, name="nor_read")
        emulator.hook_function(0x08019588, self._erase, name="nor_erase_4k")
        emulator.hook_function(0x080197EC, self._program, name="nor_page_program")

    def _read(self, emulator):
        address, destination, length = (emulator.reg(i) for i in range(3))
        self._bounds(address, length)
        emulator.write(destination, bytes(self.data[address:address + length]))
        self.operations.append({"op": "read", "address": address, "length": length})

    def _erase(self, emulator):
        address = emulator.reg(0) & ~0xfff
        self._bounds(address, 4096)
        self.data[address:address + 4096] = b"\xff" * 4096
        # The intercepted wrapper also optionally invalidates its SRAM cache.
        # Observed at 0x080195ae..0x080195c4; this flag does not change erase size.
        if emulator.reg(1) == 1 and emulator.u32(RAM_BASE + 0x48) == address:
            emulator.write(RAM_BASE + 0x0d70, b"\xff" * 4096)
        self.operations.append({"op": "erase", "address": address, "length": 4096})

    def _program(self, emulator):
        source, address, length = (emulator.reg(i) for i in range(3))
        self._bounds(address, length)
        if length > 256 or address // 256 != (address + max(length - 1, 0)) // 256:
            raise EmulationError("stock page program crossed a page in this model")
        payload = emulator.read(source, length)
        for i, value in enumerate(payload):
            self.data[address + i] &= value
        self.operations.append({"op": "program", "address": address, "length": length})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path, help="decoded reference application (not supplied)")
    args = parser.parse_args(argv)
    try:
        emulator = StockEmulator(args.raw.read_bytes())
        result = emulator.initialize_data()
        report = {
            "sample_sha256": emulator.sample_sha256,
            "cpu_model": "Unicorn Cortex-M0 (function-level, not PY32 peripherals)",
            "scatter_initialization": result.__dict__,
            "initialized_data_sha256": hashlib.sha256(emulator.read(RAM_BASE, 0x4c4)).hexdigest(),
            "stock_software_clock_hz": emulator.u32(RAM_BASE),
            "stock_nor_cache_address": f"0x{emulator.u32(RAM_BASE + 0x48):08x}",
            "hardware_accesses": "none; MMIO remains unmapped",
        }
    except (OSError, EmulationError) as error:
        print(f"emulate_stock: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
