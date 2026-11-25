/* SPDX-License-Identifier: MIT */
#ifndef K10_HAL_TEST_MMIO_H
#define K10_HAL_TEST_MMIO_H

#include <stdint.h>

uint32_t mmio_read32(uintptr_t address);
void mmio_write32(uintptr_t address, uint32_t value);
void mmio_modify32(uintptr_t address, uint32_t clear_bits, uint32_t set_bits);
void k10_test_cpu_instructions(const char *sequence);

#define K10_HAL_CPU_INSTRUCTIONS(sequence) k10_test_cpu_instructions(sequence)

#endif
