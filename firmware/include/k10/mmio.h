/* SPDX-License-Identifier: MIT */
#ifndef K10_MMIO_H
#define K10_MMIO_H

#include <stdint.h>

/* Access width matters for peripheral data registers, especially SPI. */
static inline uint8_t mmio_read8(uintptr_t address)
{
    return *(volatile uint8_t *)address;
}

static inline void mmio_write8(uintptr_t address, uint8_t value)
{
    *(volatile uint8_t *)address = value;
}

static inline uint16_t mmio_read16(uintptr_t address)
{
    return *(volatile uint16_t *)address;
}

static inline void mmio_write16(uintptr_t address, uint16_t value)
{
    *(volatile uint16_t *)address = value;
}

static inline uint32_t mmio_read32(uintptr_t address)
{
    return *(volatile uint32_t *)address;
}

static inline void mmio_write32(uintptr_t address, uint32_t value)
{
    *(volatile uint32_t *)address = value;
}

/* Not atomic. Protect shared registers from competing interrupt handlers. */
static inline void mmio_modify32(uintptr_t address, uint32_t clear_bits,
                                 uint32_t set_bits)
{
    mmio_write32(address, (mmio_read32(address) & ~clear_bits) | set_bits);
}

#endif
