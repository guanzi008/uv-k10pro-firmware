#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Replay isolated K10 Pro LCD/backlight functions against explicit MMIO.

The vendor image is supplied locally and digest pinned by StockEmulator. SPI
status is synthetic, delays are intercepted, and timer registers do not advance.
This proves the instructions' register/byte behavior, not physical wiring or
electrical compatibility. No vendor code or font data is included in this file.
"""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_PROT_READ, UC_PROT_WRITE
from unicorn.arm_const import UC_ARM_REG_PC

from emulate_stock import EmulationError, NorModel, RETURN_ADDRESS, SCRATCH_BASE, StockEmulator


FUNCTIONS = {
    "gpio_init": 0x08018B94,
    "lcd_partial": 0x08019390,
    "lcd_pages_1_7": 0x080193E0,
    "lcd_page_0": 0x08019434,
    "lcd_clear": 0x08019478,
    "lcd_init": 0x080194A0,
    "lcd_address": 0x0801954C,
    "lcd_command": 0x08019574,
    "spi_init": 0x0801A1D8,
    "spi_byte": 0x0801A27C,
    "backlight_init": 0x0801A5F4,
    "backlight_set": 0x0801A710,
    "backlight_apply_settings": 0x0800E36C,
    "delay": 0x0801D000,
}
GPIO = {"A": 0x50000000, "B": 0x50000400, "C": 0x50000800, "F": 0x50001400}
SPI1 = 0x40013000
TIMERS = {"TIM1": 0x40012C00, "TIM2": 0x40000000, "TIM3": 0x40000400}
FRAMEBUFFER = 0x20000D70
CACHE_TAG = 0x20000048


class DisplayMMIO:
    """Register storage plus GPIO latches and synchronous synthetic SPI.

    Every CPU access is checked against named peripheral register offsets.
    Reserved addresses fault. Reading SPI SR returns TXE|RXNE; each DR write
    records one LCD byte, with the current PB2/PF2 latches. This is intentionally
    not a peripheral timing, interrupt, DMA, or LCD panel emulator.
    """

    GPIO_OFFSETS = {0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x20, 0x24, 0x28}
    SPI_OFFSETS = {0x00, 0x04, 0x08, 0x0C, 0x10, 0x1C}
    TIMER_OFFSETS = {0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C,
                     0x20, 0x24, 0x28, 0x2C, 0x30, 0x34, 0x38, 0x3C,
                     0x40, 0x44, 0x48, 0x4C}

    def __init__(self, emulator):
        self.emulator = emulator
        self.writes = []
        self.spi_bytes = []
        self.delays = []
        self.transaction = 0
        self._peripherals = {0x40021000: ("RCC", {0x34, 0x3C, 0x40}),
                             SPI1: ("SPI1", self.SPI_OFFSETS)}
        self._peripherals.update({base: (f"GPIO{port}", self.GPIO_OFFSETS)
                                  for port, base in GPIO.items()})
        self._peripherals.update({base: (name, self.TIMER_OFFSETS)
                                  for name, base in TIMERS.items()})
        self._registers = {base + offset: (name, offset)
                           for base, (name, offsets) in self._peripherals.items()
                           for offset in offsets}
        pages = sorted({address & ~0xFFF for address in self._registers})
        for page in pages:
            emulator.uc.mem_map(page, 0x1000, UC_PROT_READ | UC_PROT_WRITE)
            emulator.uc.hook_add(UC_HOOK_MEM_READ, self._read,
                                 begin=page, end=page + 0xFFF)
            emulator.uc.hook_add(UC_HOOK_MEM_WRITE, self._write,
                                 begin=page, end=page + 0xFFF)
        emulator.hook_function(FUNCTIONS["delay"], self._delay, name="display_delay_argument")

    def _validate(self, address, size):
        register = address & ~3
        if register not in self._registers or size not in (1, 2, 4) or (address & 3) + size > 4:
            raise EmulationError(f"unmodeled display MMIO 0x{address:08x}/{size}")
        if address != register:
            raise EmulationError(f"unmodeled subregister display MMIO 0x{address:08x}/{size}")
        return self._registers[register]

    def _fault(self, message):
        self.emulator._fault = message
        self.emulator.uc.emu_stop()

    def _read(self, uc, access, address, size, value, user_data):
        del access, value, user_data
        try:
            name, offset = self._validate(address, size)
            if name == "SPI1" and offset == 0x08:
                self.emulator.put_u32(address, 3)  # Synthetic TXE and RXNE.
            elif name == "SPI1" and offset == 0x0C:
                self.emulator.put_u32(address, 0)  # Unconnected/synthetic receive byte.
        except EmulationError as error:
            self._fault(str(error))

    def _write(self, uc, access, address, size, value, user_data):
        del access, user_data
        try:
            name, offset = self._validate(address, size)
            self.writes.append({"peripheral": name, "offset": offset, "size": size,
                                "value": value, "pc": f"0x{uc.reg_read(UC_ARM_REG_PC):08x}"})
            if name.startswith("GPIO") and offset in (0x18, 0x28):
                base = address - offset
                old = self.emulator.u32(base + 0x14)
                if offset == 0x18:
                    new = (old | (value & 0xFFFF)) & ~(value >> 16)
                else:
                    new = old & ~(value & 0xFFFF)
                self.emulator.put_u32(base + 0x14, new & 0xFFFF)
                if base == GPIO["B"] and old & 4 and not new & 4:
                    self.transaction += 1
            elif name == "SPI1" and offset == 0x0C:
                if size != 1:
                    raise EmulationError("LCD SPI DR write is not one byte")
                self.spi_bytes.append({"value": value & 0xFF,
                                       "cs": self.level("B", 2),
                                       "dc": self.level("F", 2),
                                       "transaction": self.transaction})
        except EmulationError as error:
            self._fault(str(error))

    def _delay(self, emulator):
        self.delays.append(emulator.reg(0))

    def level(self, port, pin):
        return (self.emulator.u32(GPIO[port] + 0x14) >> pin) & 1

    def pin(self, port, pin):
        base = GPIO[port]
        return {"pin": f"P{port}{pin}",
                "mode": (self.emulator.u32(base) >> (pin * 2)) & 3,
                "otype": (self.emulator.u32(base + 4) >> pin) & 1,
                "speed": (self.emulator.u32(base + 8) >> (pin * 2)) & 3,
                "pull": (self.emulator.u32(base + 0x0C) >> (pin * 2)) & 3,
                "af": (self.emulator.u32(base + 0x20 + 4 * (pin // 8)) >> ((pin % 8) * 4)) & 15,
                "level": self.level(port, pin)}

    def segments(self, start=0):
        result = []
        for byte in self.spi_bytes[start:]:
            key = (byte["cs"], byte["dc"], byte["transaction"])
            if not result or key != (result[-1]["cs"], result[-1]["dc"], result[-1]["transaction"]):
                result.append({"cs": byte["cs"], "dc": byte["dc"],
                               "transaction": byte["transaction"], "data": []})
            result[-1]["data"].append(byte["value"])
        return result


def _segment_summary(segment):
    payload = bytes(segment["data"])
    summary = {"cs": segment["cs"], "dc": segment["dc"],
               "transaction": segment["transaction"], "length": len(payload)}
    if segment["dc"]:
        summary["sha256"] = hashlib.sha256(payload).hexdigest()
    else:
        summary["commands_hex"] = payload.hex(" ")
    return summary


def settings_to_pwm_cases(emulator):
    """Execute only the identified load/validation window, then the real setter.

    The larger settings initializer calls other subsystems. Entry registers here
    are explicit experiment inputs: r4=shared read buffer, r5=0, r6=1. Execution
    stops before 0x0800f560; this is not a claim that the whole initializer ran.
    """
    nor = NorModel(4 * 1024 * 1024)
    nor.attach(emulator)

    def enter(uc, address, size, data):
        del uc, address, size, data
        emulator.set_reg(4, FRAMEBUFFER)
        emulator.set_reg(5, 0)
        emulator.set_reg(6, 1)

    def leave(uc, address, size, data):
        del address, size, data
        uc.reg_write(UC_ARM_REG_PC, RETURN_ADDRESS | 1)

    first = emulator.uc.hook_add(UC_HOOK_CODE, enter, begin=0x0800F376, end=0x0800F376)
    last = emulator.uc.hook_add(UC_HOOK_CODE, leave, begin=0x0800F560, end=0x0800F560)
    results = []
    try:
        for values in ((13, 47, 89), (0, 100, 1), (101, 255, 100)):
            record = bytearray(0x128)
            record[0x24:0x27] = bytes(values)
            nor.data[0x13000:0x13128] = record
            before = len(nor.operations)
            window = emulator.call(0x0800F376)
            expected = [value if value <= 100 else 50 for value in values]
            loaded = list(emulator.read(0x20002404, 3))
            assert loaded == expected
            apply = emulator.call(FUNCTIONS["backlight_apply_settings"])
            compares = [emulator.u32(TIMERS["TIM1"] + 0x3C),
                        emulator.u32(TIMERS["TIM2"] + 0x34),
                        emulator.u32(TIMERS["TIM3"] + 0x38)]
            assert compares == [value * 25 for value in expected]
            results.append({"raw_offsets": ["0x13024", "0x13025", "0x13026"],
                            "input": values, "ram_addresses": ["0x20002404", "0x20002405", "0x20002406"],
                            "loaded": loaded, "compare": compares,
                            "window_call": asdict(window), "apply_call": asdict(apply),
                            "nor_operations": nor.operations[before:]})
    finally:
        emulator.uc.hook_del(first)
        emulator.uc.hook_del(last)
    return {"window_start": "0x0800f376", "window_stop_before": "0x0800f560",
            "injected_registers": {"r4": f"0x{FRAMEBUFFER:08x}", "r5": 0, "r6": 1},
            "scope": "bounded stock instruction window, not the whole settings initializer",
            "cases": results}


def run_experiments(raw):
    emulator = StockEmulator(raw)
    initialization = emulator.initialize_data()
    emulator.write(FRAMEBUFFER, b"\xAA" * 1024)
    emulator.put_u32(CACHE_TAG, 0x13000)
    mmio = DisplayMMIO(emulator)
    gpio = emulator.call(FUNCTIONS["gpio_init"])
    lcd = emulator.call(FUNCTIONS["lcd_init"], limit=250000)
    init_segments = mmio.segments()
    assert all(item["cs"] == 0 for item in mmio.spi_bytes)
    assert len(mmio.spi_bytes) == 1067
    assert [item["value"] for item in mmio.spi_bytes[:17]] == [
        0xE2, 0xA2, 0xC0, 0xA1, 0xA6, 0xA4, 0x24, 0x81, 0x1F,
        0x2B, 0x2E, 0x2F, 0x2F, 0x2F, 0x2F, 0x40, 0xAF]
    assert mmio.delays == [120, 1, 1, 40]
    assert emulator.u32(0x2000000C) == FRAMEBUFFER
    assert emulator.u32(0x20000010) == FRAMEBUFFER + 128
    assert emulator.read(FRAMEBUFFER, 1024) == bytes(1024)
    assert emulator.u32(CACHE_TAG) == 0xFFFFFFFF
    assert emulator.u32(SPI1) == 0x35F
    assert init_segments[0]["data"] == [
        0xE2, 0xA2, 0xC0, 0xA1, 0xA6, 0xA4, 0x24, 0x81, 0x1F,
        0x2B, 0x2E, 0x2F, 0x2F, 0x2F, 0x2F, 0x40, 0xAF]
    assert [s["transaction"] for s in init_segments if s["dc"]] == [2] + [3] * 7

    spi = {"cr1": f"0x{emulator.u32(SPI1):08x}",
           "cr2": f"0x{emulator.u32(SPI1 + 4):08x}",
           "i2scfgr": f"0x{emulator.u32(SPI1 + 0x1C):08x}"}
    lcd_pins = [mmio.pin("A", 5), mmio.pin("A", 7),
                mmio.pin("B", 2), mmio.pin("F", 2)]

    pattern = bytes((i * 37 + 11) & 255 for i in range(1024))
    emulator.write(FRAMEBUFFER, pattern)
    start = len(mmio.spi_bytes)
    page_zero = emulator.call(FUNCTIONS["lcd_page_0"])
    other_pages = emulator.call(FUNCTIONS["lcd_pages_1_7"])
    frame_segments = mmio.segments(start)
    assert bytes(b for s in frame_segments if s["dc"] for b in s["data"]) == pattern
    assert [s["data"] for s in frame_segments if not s["dc"]] == [
        [0x40, 0xB0, 0x10, 4], [0x40, 0xB1, 0x10, 4],
        *[[0xB0 + page, 0x10, 4] for page in range(2, 8)]]

    partial_results = []
    example = b"\x01\x80\x55\xaa\xff"
    emulator.write(SCRATCH_BASE, example)
    for flag in (0, 1):
        start = len(mmio.spi_bytes)
        call = emulator.call(FUNCTIONS["lcd_partial"], (7, 3, len(example), SCRATCH_BASE),
                             stack_args=(flag,))
        segments = mmio.segments(start)
        assert [segment["data"] for segment in segments] == [
            [0xB3, 0x10, 0x0B], list(example if flag == 0 else bytes(len(example)))]
        assert all(byte["cs"] == 0 for byte in mmio.spi_bytes[start:])
        assert mmio.level("B", 2) == 1
        partial_results.append({"flag": flag, "x": 7, "page": 3, "length": 5,
                                "call": asdict(call), "segments": segments})

    emulator.write(FRAMEBUFFER + 1024, b"\x5A" * 32)
    emulator.put_u32(CACHE_TAG, 0x34000)
    start = len(mmio.spi_bytes)
    clear = emulator.call(FUNCTIONS["lcd_clear"], (0xA5,))
    assert emulator.read(FRAMEBUFFER, 1024) == b"\xA5" * 1024
    assert emulator.read(FRAMEBUFFER + 1024, 32) == b"\x5A" * 32
    assert emulator.u32(CACHE_TAG) == 0xFFFFFFFF
    assert bytes(byte["value"] for byte in mmio.spi_bytes[start:] if byte["dc"]) == b"\xA5" * 1024

    backlight_init = emulator.call(FUNCTIONS["backlight_init"])
    timer_init = {name: {f"0x{offset:02x}": f"0x{emulator.u32(base + offset):08x}"
                         for offset in sorted(DisplayMMIO.TIMER_OFFSETS)
                         if emulator.u32(base + offset)}
                  for name, base in TIMERS.items()}
    backlight_pins = [mmio.pin("B", 6), mmio.pin("A", 15), mmio.pin("B", 5)]
    backlight_cases = []
    for values in ((0, 0, 0), (1, 50, 100), (100, 0, 0), (0, 100, 0), (0, 0, 100)):
        call = emulator.call(FUNCTIONS["backlight_set"], values)
        actual = [emulator.u32(TIMERS["TIM1"] + 0x3C),
                  emulator.u32(TIMERS["TIM2"] + 0x34),
                  emulator.u32(TIMERS["TIM3"] + 0x38)]
        assert actual == [value * 25 for value in values]
        backlight_cases.append({"arguments": values, "compare": actual, "call": asdict(call)})

    emulator.write(0x20002401, b"\0")  # Disable the conditional state gate.
    emulator.write(0x20002404, bytes((13, 47, 89)))
    settings_call = emulator.call(FUNCTIONS["backlight_apply_settings"])
    settings_compare = [emulator.u32(TIMERS["TIM1"] + 0x3C),
                        emulator.u32(TIMERS["TIM2"] + 0x34),
                        emulator.u32(TIMERS["TIM3"] + 0x38)]
    assert settings_compare == [325, 1175, 2225]
    settings_chain = settings_to_pwm_cases(emulator)

    return {"passed": 14,
            "scenario_counts": {"lcd_initialization": 1, "pattern_frame": 1,
                                "partial_refresh": len(partial_results), "clear_fill": 1,
                                "direct_pwm": len(backlight_cases), "ram_settings_apply": 1,
                                "nor_settings_to_pwm": len(settings_chain["cases"])},
            "sample": {"sha256": emulator.sample_sha256, "version": "K10 Pro ENG 8.02.16",
                       "application_base": "0x08002000"},
            "boundary": {"cpu": "actual supplied Thumb instructions",
                         "gpio": "register file plus BSRR/BRR output latch effects",
                         "spi": "SR TXE/RXNE forced ready, DR reads zero; no physical bit timing",
                         "timer": "register file only; counters do not advance",
                         "delay": "intercepted and arguments recorded; no elapsed target time",
                         "not_modeled": ["LCD electronics", "RF", "DMA", "interrupts", "PLL", "bootloader"]},
            "functions": {name: f"0x{address:08x}" for name, address in FUNCTIONS.items()},
            "calls": {"scatter": asdict(initialization), "gpio": asdict(gpio), "lcd": asdict(lcd),
                      "page_zero": asdict(page_zero), "other_pages": asdict(other_pages),
                      "clear_fill": asdict(clear),
                      "backlight_init": asdict(backlight_init)},
            "lcd": {"pins": lcd_pins, "spi": spi, "delays": mmio.delays,
                    "initialization_byte_count": sum(len(s["data"]) for s in init_segments),
                    "initialization_segments": [_segment_summary(s) for s in init_segments],
                    "framebuffer": {"base": f"0x{FRAMEBUFFER:08x}", "size": 1024,
                                    "width_bytes": 128, "pages": 8,
                                    "page_zero_pointer": "0x2000000c", "other_pages_pointer": "0x20000010",
                                    "shared_with_nor_cache": True, "cache_tag_invalidated": "0x20000048"},
                    "pattern_refresh": [_segment_summary(s) for s in frame_segments],
                    "partial_refresh": partial_results},
            "backlight": {"pins_in_argument_order": backlight_pins,
                          "timer_registers_after_init": timer_init,
                          "cases": backlight_cases,
                          "settings_apply": {"addresses": ["0x20002404", "0x20002405", "0x20002406"],
                                             "values": [13, 47, 89], "compare": settings_compare,
                                             "call": asdict(settings_call)},
                          "settings_to_pwm": settings_chain},
            "mmio_write_count": len(mmio.writes)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path, help="locally supplied, decoded 8.02.16 application")
    parser.add_argument("--output", type=Path, help="write JSON evidence here")
    args = parser.parse_args()
    result = run_experiments(args.raw.read_bytes())
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
