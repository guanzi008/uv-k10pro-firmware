/* SPDX-License-Identifier: MIT */
#ifndef K10_MMIO_H
#define K10_MMIO_H
#include <stdint.h>
uint8_t mmio_read8(uintptr_t address);
void mmio_write8(uintptr_t address, uint8_t value);
uint32_t mmio_read32(uintptr_t address);
void mmio_write32(uintptr_t address, uint32_t value);
static inline void mmio_modify32(uintptr_t address, uint32_t clear_bits,
                                 uint32_t set_bits)
{
    mmio_write32(address, (mmio_read32(address) & ~clear_bits) | set_bits);
}
#endif
