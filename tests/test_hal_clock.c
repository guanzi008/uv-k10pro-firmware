/* SPDX-License-Identifier: MIT */
/*
 * Compile the actual HAL into this test translation unit. Only MMIO and CPU
 * instructions come from tests/hal_mocks; the clock decoder, timer setup and
 * delays below are the production implementation, not a second algorithm.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../firmware/src/hal.c"

/* Host-only definitions for linker symbols used by the unused fault path. */
unsigned char _fault_stack_bottom[256];
unsigned char _fault_stack_top[1];

struct scenario {
    const char *name;
    uint32_t cr;
    uint32_t icscr;
    uint32_t cfgr;
    uint32_t pllcfgr;
    uint32_t expected_hz;
    bool expected_known;
};

#define HSI_READY (1u << 10)
#define HSE_READY (1u << 17)
#define PLL_READY (1u << 25)

static const struct scenario internal_cases[] = {
    {"HSI 8 MHz", HSI_READY, 1u << 13, 0, 0, 8000000, true},
    {"HSI /128, AHB /2", HSI_READY | (7u << 11), 0,
        8u << 8, 0, 15625, true},
    {"PLL HSI bypasses HSIDIV", HSI_READY | PLL_READY | (7u << 11),
        4u << 13, 2u << 3, 2, 48000000, true},
    {"PLL 16 MHz x3", HSI_READY | PLL_READY, 2u << 13,
        2u << 3, 6, 48000000, true},
    {"PLL 22.12 MHz x3", HSI_READY | PLL_READY, 3u << 13,
        2u << 3, 6, 66360000, true},
    {"PLL 24 MHz x3, AHB /128", HSI_READY | PLL_READY, 4u << 13,
        (2u << 3) | (13u << 8), 6, 562500, true},
    {"AHB encoding skips /32", HSI_READY, 4u << 13,
        12u << 8, 0, 375000, true},
    {"LSI nominal", 0, 0, 3u << 3, 0, 32768, true},
};

static const struct scenario invalid_cases[] = {
    {"Reserved HSI selection", HSI_READY, 5u << 13, 0, 0, 48000000, false},
    {"Reserved PLL source", HSI_READY | PLL_READY, 2u << 13,
        2u << 3, 1, 48000000, false},
    {"Reserved PLL multiplier 2", HSI_READY | PLL_READY, 2u << 13,
        2u << 3, 10, 48000000, false},
    {"Reserved PLL multiplier 3", HSI_READY | PLL_READY, 2u << 13,
        2u << 3, 14, 48000000, false},
    {"HSI not ready", 0, 1u << 13, 0, 0, 48000000, false},
    {"PLL not ready", HSI_READY, 2u << 13, 2u << 3, 6, 48000000, false},
    {"Reserved system clock", 0, 0, 5u << 3, 0, 48000000, false},
    {"Clock too slow for runtime", HSI_READY | (7u << 11), 0,
        15u << 8, 0, 48000000, false},
};

/* The second host build explicitly supplies a different external frequency. */
#ifdef K10_HSE_HZ
static const struct scenario external_cases[] = {
    {"Board-qualified HSE", HSE_READY, 0, 1u << 3, 0, 12000000, true},
    {"Board-qualified PLL HSE", HSE_READY | PLL_READY, 0,
        2u << 3, 7, 36000000, true},
    {"Board-qualified LSE", 0, 0, 4u << 3, 0, 32000, true},
};
#else
static const struct scenario external_cases[] = {
    {"Assumed HSE", HSE_READY, 0, 1u << 3, 0, 16000000, false},
    {"Assumed PLL HSE", HSE_READY | PLL_READY, 0,
        2u << 3, 7, 48000000, false},
    {"Assumed LSE", 0, 0, 4u << 3, 0, 32768, false},
};
#endif

static const struct scenario *active_case;
static unsigned write_count;
static unsigned cfgr_reads;
static unsigned cpu_instruction_count;
static bool unstable_snapshot;
static uint32_t mock_value;
static uint32_t mock_load;
static uint32_t mock_ctrl;
static uint64_t elapsed_cycles;

#define REQUIRE(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "%s:%d: %s: %s\n", __FILE__, __LINE__, \
                active_case ? active_case->name : "test setup", #condition); \
        exit(EXIT_FAILURE); \
    } \
} while (0)

uint32_t mmio_read32(uintptr_t address)
{
    switch (address) {
    case RCC_CR: return active_case->cr;
    case RCC_ICSCR: return active_case->icscr;
    case RCC_CFGR:
        return active_case->cfgr
            ^ ((unstable_snapshot && cfgr_reads++ != 0u) ? 0x100u : 0u);
    case RCC_PLLCFGR: return active_case->pllcfgr;
    case SCB_SHPR3: return 0x00010000u;
    case SYSTICK_VAL: {
        uint32_t previous = mock_value;
        uint32_t step = tick_period > 17u ? 17u : 1u;
        mock_value = (mock_value + tick_period - step) % tick_period;
        elapsed_cycles += step;
        return previous;
    }
    default:
        REQUIRE(!"Unexpected MMIO read");
        return 0;
    }
}

void mmio_write32(uintptr_t address, uint32_t value)
{
    ++write_count;
    switch (address) {
    case SYSTICK_CTRL:
        REQUIRE(value == 0u || value == 7u);
        mock_ctrl = value;
        break;
    case SYSTICK_LOAD:
        mock_load = value;
        REQUIRE(value < 0x01000000u);
        break;
    case SYSTICK_VAL:
        REQUIRE(value == 0u);
        mock_value = 0u;
        break;
    case SCB_SHPR3:
        REQUIRE(value == 0xc0010000u);
        break;
    default:
        /* In particular, no inherited RCC register may be written. */
        REQUIRE(!"Unexpected MMIO write");
    }
}

void mmio_modify32(uintptr_t address, uint32_t clear_bits, uint32_t set_bits)
{
    mmio_write32(address, (mmio_read32(address) & ~clear_bits) | set_bits);
}

void k10_test_cpu_instructions(const char *sequence)
{
    (void)sequence;
    ++cpu_instruction_count;
}

static void start_case(const struct scenario *item, bool unstable)
{
    active_case = item;
    write_count = 0u;
    cfgr_reads = 0u;
    cpu_instruction_count = 0u;
    elapsed_cycles = 0u;
    unstable_snapshot = unstable;
    /* A fresh MCU startup would zero this production .bss variable. */
    initialized = false;
    hal_early_init();
    REQUIRE(write_count == 5u);
    REQUIRE(cpu_instruction_count == 0u);
    REQUIRE(mock_ctrl == 7u);
    REQUIRE(mock_load + 1u == tick_period);
}

static void check_clock_cases(const struct scenario *items, size_t count)
{
    for (size_t i = 0; i < count; ++i) {
        start_case(&items[i], false);
        REQUIRE(hal_clock_hz() == items[i].expected_hz);
        REQUIRE(hal_clock_known() == items[i].expected_known);
    }
}

static void check_timer(void)
{
    for (size_t i = 0; i < sizeof internal_cases / sizeof internal_cases[0]; ++i) {
        const struct scenario *item = &internal_cases[i];
        start_case(item, false);
        uint32_t expected_ms = item->expected_hz < 100000u ? 100u
            : (item->expected_hz < 1000000u ? 10u : 1u);
        REQUIRE(mock_load + 1u == (item->expected_hz * expected_ms + 500u) / 1000u);
        REQUIRE(hal_millis() == 0u);
        SysTick_Handler();
        REQUIRE(hal_millis() == expected_ms);
        milliseconds = UINT32_MAX - expected_ms + 1u;
        SysTick_Handler();
        REQUIRE(hal_millis() == 0u);

        hal_early_init();
        REQUIRE(write_count == 5u); /* No timer reset on a repeated call. */
        REQUIRE(hal_millis() == 0u);

        hal_delay_us(0u);
        REQUIRE(elapsed_cycles == 0u);
        hal_delay_us(1000u); /* Starts at zero and crosses the reload boundary. */
        REQUIRE(elapsed_cycles >= item->expected_hz / 1000u);
        elapsed_cycles = 0u;
        hal_delay_ms(2u);
        REQUIRE(elapsed_cycles >= item->expected_hz / 500u);
        REQUIRE(hal_millis() == 0u); /* No delivered IRQs are needed by delays. */
    }
}

int main(int argc, char **argv)
{
    if (argc != 2)
        return 2;
    if (strcmp(argv[1], "internal") == 0) {
        check_clock_cases(internal_cases, sizeof internal_cases / sizeof internal_cases[0]);
    } else if (strcmp(argv[1], "invalid") == 0) {
        check_clock_cases(invalid_cases, sizeof invalid_cases / sizeof invalid_cases[0]);
    } else if (strcmp(argv[1], "external") == 0) {
        check_clock_cases(external_cases, sizeof external_cases / sizeof external_cases[0]);
    } else if (strcmp(argv[1], "transition") == 0) {
        start_case(&internal_cases[0], true);
        REQUIRE(hal_clock_hz() == 48000000u);
        REQUIRE(!hal_clock_known());
    } else if (strcmp(argv[1], "timer") == 0) {
        check_timer();
    } else {
        return 2;
    }
    printf("PASS: HAL %s\n", argv[1]);
    return 0;
}
