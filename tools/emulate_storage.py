#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Replay the stock NOR read/modify/write routine against synthetic flash only."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

from emulate_stock import EmulationError, NorModel, SCRATCH_BASE, StockEmulator

RMW = 0x08019694
SECTOR = 4096


def run_storage(raw: bytes) -> dict:
    cases = []

    def setup():
        em = StockEmulator(raw)
        em.initialize_data()
        nor = NorModel()
        nor.attach(em)
        return em, nor

    def call(em, nor, address, payload, mode):
        em.write(SCRATCH_BASE, payload)
        result = em.call(RMW, (address, SCRATCH_BASE, len(payload), mode))
        return result

    def record(name, em, nor, result, expected, observation):
        # Compare the entire model, not only the requested output bytes.
        if nor.data != expected:
            first = next(i for i, (a, b) in enumerate(zip(nor.data, expected)) if a != b)
            raise EmulationError(f"{name}: unexpected synthetic NOR byte at 0x{first:x}")
        cases.append({
            "name": name, "passed": True, "observation": observation,
            "instructions": result.instructions,
            "operations": [dict(operation) for operation in nor.operations],
            "result_sha256": hashlib.sha256(nor.data).hexdigest(),
        })

    # Erased target: program the requested bytes only, splitting at page edges.
    em, nor = setup()
    address, payload = 0x12F8, bytes(range(32))
    expected = nor.data.copy()
    expected[address:address + len(payload)] = payload
    result = call(em, nor, address, payload, 0)
    assert [(o["address"], o["length"]) for o in nor.operations if o["op"] == "program"] == [(0x12F8, 8), (0x1300, 24)]
    assert not any(o["op"] == "erase" for o in nor.operations)
    record("erased_target_page_split", em, nor, result, expected,
           "Programs 8 + 24 bytes across a 256-byte page; no sector erase.")

    # Same input in a second call: the sector cache is reused; no I/O is needed.
    nor.operations.clear()
    result = call(em, nor, address, payload, 0)
    assert not nor.operations
    record("identical_cached_write", em, nor, result, expected,
           "Identical cached bytes cause no read, erase, or program.")

    # A non-FF old byte triggers erase even if the new byte only clears bits.
    em, nor = setup()
    nor.data[0x1000:0x2000] = bytes((i * 7 + 3) & 255 for i in range(SECTOR))
    address, payload = 0x1234, b"\x00" * 32
    expected = nor.data.copy()
    expected[address:address + len(payload)] = payload
    result = call(em, nor, address, payload, 0)
    assert sum(o["op"] == "erase" for o in nor.operations) == 1
    assert sum(o["length"] for o in nor.operations if o["op"] == "program") == SECTOR
    record("mode0_preserves_sector_neighbors", em, nor, result, expected,
           "A changed non-erased target causes one erase and a complete 4 KiB rewrite; both neighbors survive.")

    # Mode 1 preserves the prefix but leaves the sector tail erased after erase.
    em, nor = setup()
    nor.data[0x50000:0x51000] = b"\x55" * SECTOR
    address, payload = 0x50120, b"\xa6" * 32
    expected = nor.data.copy()
    expected[address:address + len(payload)] = payload
    expected[address + len(payload):0x51000] = b"\xff" * (0x51000 - address - len(payload))
    result = call(em, nor, address, payload, 1)
    assert sum(o["length"] for o in nor.operations if o["op"] == "program") == 0x140
    record("mode1_erased_tail", em, nor, result, expected,
           "After erase, only the prefix through the requested end is rewritten; the rest of this sector becomes FF.")
    assert em.read(0x20000D70, SECTOR) == bytes(nor.data[0x50000:0x51000])

    # Mode 1 does not truncate a sector when the target was already erased.
    em, nor = setup()
    nor.data[0x50140:0x51000] = b"\x33" * (0x51000 - 0x50140)
    address, payload = 0x50120, b"\x5a" * 32
    expected = nor.data.copy()
    expected[address:address + len(payload)] = payload
    result = call(em, nor, address, payload, 1)
    assert not any(o["op"] == "erase" for o in nor.operations)
    record("mode1_erased_target_preserves_tail", em, nor, result, expected,
           "An already-erased target takes the direct-program path; the populated tail survives.")

    for mode in (0, 1):
        em, nor = setup()
        nor.data[0x50000:0x52000] = b"\x66" * (2 * SECTOR)
        address, payload = 0x50FF0, bytes(range(32))
        expected = nor.data.copy()
        expected[address:address + len(payload)] = payload
        if mode == 1:
            expected[0x51010:0x52000] = b"\xff" * 0xFF0
        result = call(em, nor, address, payload, mode)
        assert [o["address"] for o in nor.operations if o["op"] == "erase"] == [0x50000, 0x51000]
        record(f"cross_sector_mode{mode}", em, nor, result, expected,
               "Splits a 32-byte request at the 4 KiB boundary; " +
               ("both sectors retain all neighbors." if mode == 0 else "the second sector's trailing bytes become FF."))

    # Cache persists between two different modifications in the same sector.
    em, nor = setup()
    expected = nor.data.copy()
    call(em, nor, 0x1100, b"\xa5\x5a", 0)
    result = call(em, nor, 0x1200, b"\x12\x34", 0)
    expected[0x1100:0x1102] = b"\xa5\x5a"
    expected[0x1200:0x1202] = b"\x12\x34"
    assert sum(o["op"] == "read" for o in nor.operations) == 1
    record("same_sector_cache_reuse", em, nor, result, expected,
           "Two different writes reuse one sector read and both changes persist in the model.")

    return {
        "sample_sha256": StockEmulator(raw).sample_sha256,
        "function": "0x08019694",
        "argument_order": ["address", "source_pointer", "length", "mode"],
        "executed": ["stock RMW", "stock memory helpers", "stock page-splitting wrapper 0x080197b2"],
        "replaced": ["0x080195e4 read", "0x08019588 sector erase", "0x080197ec page program"],
        "synthetic_capacity_bytes": 0x200000,
        "limitations": "Function-level CPU replay with a byte-array NOR model; no real device, SPI timing, power loss, endurance or fitted capacity validation.",
        "cases": cases,
        "passed": len(cases),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = run_storage(args.raw.read_bytes())
        rendered = json.dumps(report, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    except (OSError, ValueError, AssertionError, EmulationError) as error:
        print(f"emulate_storage: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
