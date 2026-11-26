/* SPDX-License-Identifier: MIT
 * Execute the real display.c against a bounded, explicitly synthetic bus. */
#include "k10/board.h"
#include "k10/display.h"
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct reg_entry { uintptr_t address; uint32_t value; };
static struct reg_entry registers[256];
static size_t register_count;
static unsigned mmio_accesses;
static unsigned status_reads;
static uint32_t ticks;
static bool frozen_tick;
static unsigned rx_pending;
static unsigned rx_reads;
static unsigned busy_remaining;
static unsigned early_cs_release;
static unsigned cs_assertions;
static unsigned cs_releases;
static unsigned delays[16];
static size_t delay_count;
enum fault { FAULT_NONE, FAULT_TXE, FAULT_RXNE, FAULT_BSY, FAULT_OVR };
static enum fault injected_fault;
static size_t fault_after;
struct lcd_byte { uint8_t value; bool data; unsigned transaction; };
static struct lcd_byte output[8192];
static size_t output_count;

static uint32_t *reg(uintptr_t address)
{
    for (size_t i = 0; i < register_count; ++i)
        if (registers[i].address == address)
            return &registers[i].value;
    assert(register_count < sizeof(registers) / sizeof(registers[0]));
    registers[register_count] = (struct reg_entry){address, 0u};
    return &registers[register_count++].value;
}

static uintptr_t gpio_base(uintptr_t address)
{
    const uintptr_t bases[] = {K10_GPIOA_BASE, K10_GPIOB_BASE, K10_GPIOF_BASE};
    for (size_t i = 0; i < sizeof(bases) / sizeof(bases[0]); ++i)
        if (address >= bases[i] && address < bases[i] + 0x2cu)
            return bases[i];
    return 0u;
}

static bool selected(void)
{
    return (*reg(K10_GPIOB_BASE + 0x14u) & (1u << K10_LCD_CS_PIN)) == 0u;
}

static bool fault_active(enum fault fault)
{
    return injected_fault == fault && output_count >= fault_after;
}

static void reset_peripheral(uintptr_t base)
{
    for (size_t i = 0; i < register_count; ++i)
        if (registers[i].address >= base && registers[i].address < base + 0x50u)
            registers[i].value = 0u;
    if (base == K10_SPI1_BASE) {
        rx_pending = 0u;
        busy_remaining = 0u;
    }
}

uint32_t mmio_read32(uintptr_t address)
{
    ++mmio_accesses;
    if (address == K10_SPI1_BASE + 0x08u) {
        ++status_reads;
        uint32_t status = fault_active(FAULT_TXE) ? 0u : 2u;
        if (rx_pending != 0u && !fault_active(FAULT_RXNE))
            status |= 1u;
        if (busy_remaining != 0u || fault_active(FAULT_BSY))
            status |= 0x80u;
        if (busy_remaining != 0u)
            --busy_remaining;
        if (fault_active(FAULT_OVR))
            status |= 0x40u;
        return status;
    }
    return *reg(address);
}

uint8_t mmio_read8(uintptr_t address)
{
    ++mmio_accesses;
    assert(address == K10_SPI1_BASE + 0x0cu);
    assert(rx_pending != 0u);
    --rx_pending;
    ++rx_reads;
    return 0u;
}

void mmio_write32(uintptr_t address, uint32_t value)
{
    ++mmio_accesses;
    uintptr_t base = gpio_base(address);
    if (base != 0u) {
        unsigned offset = (unsigned)(address - base);
        assert(offset == 0u || offset == 4u || offset == 8u || offset == 0x0cu ||
               offset == 0x18u || offset == 0x20u || offset == 0x24u || offset == 0x28u);
        if (offset == 0x18u || offset == 0x28u) {
            /* Driver may only touch CS#/D/C output latches, not other board signals. */
            assert((base == K10_GPIOB_BASE && value == (1u << K10_LCD_CS_PIN)) ||
                   (base == K10_GPIOF_BASE && value == (1u << K10_LCD_DC_PIN)));
            bool was_selected = selected();
            if (offset == 0x18u)
                *reg(base + 0x14u) |= value;
            else
                *reg(base + 0x14u) &= ~value;
            bool now_selected = selected();
            if (!was_selected && now_selected)
                ++cs_assertions;
            if (was_selected && !now_selected) {
                ++cs_releases;
                if (busy_remaining != 0u)
                    ++early_cs_release;
            }
        }
    } else if (address >= K10_RCC_BASE && address <= K10_RCC_BASE + 0x40u) {
        unsigned offset = (unsigned)(address - K10_RCC_BASE);
        assert(offset == 0x2cu || offset == 0x30u || offset == 0x34u || offset == 0x3cu || offset == 0x40u);
        if (offset == 0x2cu) {
            if ((value & 1u) != 0u)
                reset_peripheral(K10_TIM2_BASE);
            if ((value & 2u) != 0u)
                reset_peripheral(K10_TIM3_BASE);
        } else if (offset == 0x30u) {
            if ((value & (1u << 11)) != 0u)
                reset_peripheral(K10_TIM1_BASE);
            if ((value & (1u << 12)) != 0u)
                reset_peripheral(K10_SPI1_BASE);
        }
    } else if (address >= K10_SPI1_BASE && address < K10_SPI1_BASE + 0x20u) {
        unsigned offset = (unsigned)(address - K10_SPI1_BASE);
        assert(offset == 0u || offset == 4u || offset == 0x1cu);
    } else {
        const uintptr_t timer_bases[] = {K10_TIM1_BASE, K10_TIM2_BASE, K10_TIM3_BASE};
        bool recognized = false;
        for (size_t i = 0; i < sizeof(timer_bases) / sizeof(timer_bases[0]); ++i) {
            if (address < timer_bases[i] || address >= timer_bases[i] + 0x48u)
                continue;
            unsigned offset = (unsigned)(address - timer_bases[i]);
            assert(offset == 0u || offset == 0x14u || offset == 0x18u || offset == 0x1cu ||
                   offset == 0x20u || offset == 0x28u || offset == 0x2cu ||
                   offset == 0x34u || offset == 0x38u || offset == 0x3cu ||
                   (i == 0u && offset == 0x44u));
            recognized = true;
        }
        assert(recognized);
    }
    *reg(address) = value;
}

void mmio_write8(uintptr_t address, uint8_t value)
{
    ++mmio_accesses;
    assert(address == K10_SPI1_BASE + 0x0cu);
    assert(selected());
    assert(rx_pending == 0u);
    assert(output_count < sizeof(output) / sizeof(output[0]));
    output[output_count++] = (struct lcd_byte){
        value, (*reg(K10_GPIOF_BASE + 0x14u) & (1u << K10_LCD_DC_PIN)) != 0u, cs_assertions};
    rx_pending = 1u;
    busy_remaining = 5u; /* RXNE becomes set before modeled BSY clears. */
}

uint32_t hal_millis(void)
{
    uint32_t result = ticks;
    if (!frozen_tick)
        ++ticks;
    return result;
}

void hal_delay_ms(uint32_t milliseconds)
{
    assert(delay_count < sizeof(delays) / sizeof(delays[0]));
    delays[delay_count++] = milliseconds;
    ticks += milliseconds;
}

void hal_delay_us(uint32_t microseconds)
{
    (void)microseconds;
}

static void prepare(void)
{
    const uintptr_t bases[] = {K10_GPIOA_BASE, K10_GPIOB_BASE, K10_GPIOF_BASE};
    for (size_t i = 0; i < sizeof(bases) / sizeof(bases[0]); ++i) {
        *reg(bases[i]) = 0xaaaaaaaau;
        *reg(bases[i] + 0x04u) = 0xffffu;
        *reg(bases[i] + 0x08u) = 0xaaaaaaaau;
        *reg(bases[i] + 0x0cu) = 0xaaaaaaaau;
        *reg(bases[i] + 0x14u) = 0xffffu;
        *reg(bases[i] + 0x20u) = 0x11111111u;
        *reg(bases[i] + 0x24u) = 0x11111111u;
    }
    *reg(K10_RCC_BASE + 0x3cu) = 1u << 14; /* Existing SPI2 clock must remain enabled. */
    *reg(K10_RCC_BASE + 0x40u) = 1u << 14;
}

static void expect_byte(size_t *cursor, uint8_t value, bool data)
{
    assert(*cursor < output_count);
    assert(output[*cursor].value == value);
    assert(output[*cursor].data == data);
    ++*cursor;
}

static void expect_frame(size_t *cursor, const uint8_t *frame)
{
    for (unsigned page = 0; page < 8u; ++page) {
        if (page < 2u)
            expect_byte(cursor, 0x40u, false);
        expect_byte(cursor, (uint8_t)(0xb0u + page), false);
        expect_byte(cursor, 0x10u, false);
        expect_byte(cursor, 0x04u, false);
        for (unsigned x = 0; x < DISPLAY_WIDTH; ++x)
            expect_byte(cursor, frame == NULL ? 0u : frame[page * DISPLAY_WIDTH + x], true);
    }
}

static void expect_initialized(void)
{
    static const uint8_t commands[] = {0xe2u, 0xa2u, 0xc0u, 0xa1u, 0xa6u, 0xa4u,
        0x24u, 0x81u, 0x1fu, 0x2bu, 0x2eu, 0x2fu, 0x2fu, 0x2fu, 0x2fu, 0x40u, 0xafu};
    size_t cursor = 0u;
    assert(display_init());
    assert(display_last_error() == DISPLAY_OK);
    assert(output_count == 1067u);
    for (size_t i = 0; i < sizeof(commands); ++i)
        expect_byte(&cursor, commands[i], false);
    expect_frame(&cursor, NULL);
    assert(cursor == output_count);
    assert(delay_count == 4u && delays[0] == 120u && delays[1] == 1u && delays[2] == 1u && delays[3] == 40u);
    assert(cs_assertions == 3u && cs_releases == 3u && !selected());
    assert(early_cs_release == 0u && rx_pending == 0u && rx_reads == output_count);
    assert(*reg(K10_SPI1_BASE) == 0x35fu && *reg(K10_SPI1_BASE + 4u) == 0u);
    assert(*reg(K10_TIM1_BASE + 0x3cu) == 0u && *reg(K10_TIM2_BASE + 0x34u) == 0u && *reg(K10_TIM3_BASE + 0x38u) == 0u);
    assert(*reg(K10_TIM1_BASE + 0x1cu) == 0x60u && *reg(K10_TIM2_BASE + 0x18u) == 0x60u && *reg(K10_TIM3_BASE + 0x18u) == 0x6000u);
    assert(*reg(K10_TIM1_BASE + 0x20u) == 0x100u && *reg(K10_TIM2_BASE + 0x20u) == 1u && *reg(K10_TIM3_BASE + 0x20u) == 0x10u);
    const uintptr_t timers[] = {K10_TIM1_BASE, K10_TIM2_BASE, K10_TIM3_BASE};
    for (size_t i = 0; i < sizeof(timers) / sizeof(timers[0]); ++i) {
        assert(*reg(timers[i]) == 1u);
        assert(*reg(timers[i] + 0x28u) == 15u);
        assert(*reg(timers[i] + 0x2cu) == 2500u);
    }
    assert((*reg(K10_RCC_BASE + 0x3cu) & (1u << 14)) != 0u);
    /* Representative matrix/I2C/debug pins and other AF nibbles remain untouched. */
    assert(((*reg(K10_GPIOB_BASE) >> 6) & 15u) == 10u); /* PB3/4 */
    assert(((*reg(K10_GPIOA_BASE) >> 28) & 3u) == 2u); /* PA14 */
    assert(((*reg(K10_GPIOB_BASE + 0x24u)) & 255u) == 0x11u); /* PB8/9 */
    assert(((*reg(K10_GPIOA_BASE + 0x20u) >> 20) & 15u) == 0u); /* PA5 AF0 */
    assert(((*reg(K10_GPIOA_BASE + 0x20u) >> 28) & 15u) == 0u); /* PA7 AF0 */
    assert(((*reg(K10_GPIOB_BASE + 0x20u) >> 24) & 15u) == 11u); /* PB6 AF11 */
    assert(((*reg(K10_GPIOA_BASE + 0x24u) >> 28) & 15u) == 2u); /* PA15 AF2 */
    assert(((*reg(K10_GPIOB_BASE + 0x20u) >> 20) & 15u) == 1u); /* PB5 AF1 */
}

static void failure_case(enum fault fault, bool stop_tick)
{
    uint8_t frame[DISPLAY_FRAMEBUFFER_SIZE] = {0};
    expect_initialized();
    assert(display_set_backlight(13u, 47u, 89u));
    injected_fault = fault;
    fault_after = output_count + (fault == FAULT_TXE ? 0u : 1u);
    frozen_tick = stop_tick;
    ticks = UINT32_MAX - 4u; /* Exercise wrap-safe deadline subtraction. */
    unsigned before = status_reads;
    assert(!display_present(frame, sizeof(frame)));
    assert(display_last_error() == (fault == FAULT_OVR ? DISPLAY_BUS_ERROR : DISPLAY_TIMEOUT));
    assert(!selected());
    assert(*reg(K10_TIM1_BASE + 0x3cu) == 0u && *reg(K10_TIM2_BASE + 0x34u) == 0u && *reg(K10_TIM3_BASE + 0x38u) == 0u);
    assert(status_reads - before <= 101000u);
    if (stop_tick)
        assert(status_reads - before >= 100000u);
    unsigned after = mmio_accesses;
    assert(!display_present(frame, sizeof(frame)));
    assert(display_last_error() == DISPLAY_NOT_INITIALIZED);
    assert(mmio_accesses == after);
    injected_fault = FAULT_NONE;
    frozen_tick = false;
    assert(display_init()); /* Peripheral reset also recovers stale RX/fault state. */
    assert(display_last_error() == DISPLAY_OK);
    assert(display_present(frame, sizeof(frame)));
}

int main(int argc, char **argv)
{
    assert(argc == 2);
    prepare();
    uint8_t frame[DISPLAY_FRAMEBUFFER_SIZE];
    for (size_t i = 0; i < sizeof(frame); ++i)
        frame[i] = (uint8_t)(i * 37u + 11u);
    if (strcmp(argv[1], "disabled") == 0) {
        assert(display_last_error() == DISPLAY_DISABLED);
        assert(!display_init());
        assert(!display_present(frame, sizeof(frame)));
        assert(!display_set_backlight(25u, 25u, 25u));
        assert(display_last_error() == DISPLAY_DISABLED && mmio_accesses == 0u && delay_count == 0u);
    } else if (strcmp(argv[1], "not-initialized") == 0) {
        assert(!display_present(frame, sizeof(frame)));
        assert(!display_set_backlight(0u, 0u, 0u));
        assert(display_last_error() == DISPLAY_NOT_INITIALIZED && mmio_accesses == 0u);
    } else if (strcmp(argv[1], "normal") == 0 || strcmp(argv[1], "stale-rx") == 0) {
        expect_initialized();
        unsigned extra_rx = strcmp(argv[1], "stale-rx") == 0 ? 2u : 0u;
        rx_pending = extra_rx;
        size_t cursor = output_count;
        uint8_t original[sizeof(frame)];
        memcpy(original, frame, sizeof(frame));
        assert(display_present(frame, sizeof(frame)));
        expect_frame(&cursor, frame);
        assert(cursor == output_count && memcmp(frame, original, sizeof(frame)) == 0);
        assert(!selected() && early_cs_release == 0u);
        assert(cs_assertions == 5u && cs_releases == 5u);
        assert(rx_reads == output_count + extra_rx);
        assert(display_set_backlight(1u, 50u, 100u));
        assert(*reg(K10_TIM1_BASE + 0x3cu) == 25u && *reg(K10_TIM2_BASE + 0x34u) == 1250u && *reg(K10_TIM3_BASE + 0x38u) == 2500u);
        unsigned before = mmio_accesses;
        assert(!display_present(NULL, sizeof(frame)));
        assert(!display_present(frame, sizeof(frame) - 1u));
        assert(!display_present(frame, sizeof(frame) + 1u));
        assert(!display_set_backlight(101u, 0u, 0u));
        assert(!display_set_backlight(0u, 101u, 0u));
        assert(!display_set_backlight(0u, 0u, 255u));
        assert(display_last_error() == DISPLAY_INVALID_ARGUMENT && mmio_accesses == before);
        assert(*reg(K10_TIM1_BASE + 0x3cu) == 25u && *reg(K10_TIM2_BASE + 0x34u) == 1250u && *reg(K10_TIM3_BASE + 0x38u) == 2500u);
    } else if (strcmp(argv[1], "init-timeout") == 0) {
        injected_fault = FAULT_TXE;
        assert(!display_init());
        assert(display_last_error() == DISPLAY_TIMEOUT && !selected());
        assert(output_count == 0u && delay_count == 0u);
    } else if (strcmp(argv[1], "txe-timeout") == 0) {
        failure_case(FAULT_TXE, false);
    } else if (strcmp(argv[1], "frozen-tick") == 0) {
        failure_case(FAULT_TXE, true);
    } else if (strcmp(argv[1], "rxne-timeout") == 0) {
        failure_case(FAULT_RXNE, false);
    } else if (strcmp(argv[1], "bsy-timeout") == 0) {
        failure_case(FAULT_BSY, false);
    } else if (strcmp(argv[1], "spi-fault") == 0) {
        failure_case(FAULT_OVR, false);
    } else {
        return 2;
    }
    puts("display driver scenario passed");
    return 0;
}
