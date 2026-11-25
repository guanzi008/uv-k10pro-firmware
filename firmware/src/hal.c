/* SPDX-License-Identifier: MIT */
#include "k10/hal.h"
#include "k10/mmio.h"

/* Host tests replace only CPU instructions and MMIO, compiling this file. */
#ifndef K10_HAL_CPU_INSTRUCTIONS
#define K10_HAL_CPU_INSTRUCTIONS(sequence) \
    __asm__ volatile (sequence ::: "memory")
#endif

/*
 * Register facts: OpenPuya/PY32F071_Firmware, py32f071xB.h and
 * py32f071_ll_rcc.h. This file is an independent implementation, not a
 * redistribution of vendor source. In particular PLLMUL is at bits [3:2],
 * PLLSRC at [1:0], HSI_FS at [15:13], and HPRE at [11:8].
 */
#define RCC_BASE       UINT32_C(0x40021000)
#define RCC_CR         (RCC_BASE + UINT32_C(0x00))
#define RCC_ICSCR      (RCC_BASE + UINT32_C(0x04))
#define RCC_CFGR       (RCC_BASE + UINT32_C(0x08))
#define RCC_PLLCFGR    (RCC_BASE + UINT32_C(0x0c))
#define SYSTICK_CTRL   UINT32_C(0xe000e010)
#define SYSTICK_LOAD   UINT32_C(0xe000e014)
#define SYSTICK_VAL    UINT32_C(0xe000e018)
#define SCB_ICSR       UINT32_C(0xe000ed04)
#define SCB_SHPR3      UINT32_C(0xe000ed20)
#define UID_BASE       UINT32_C(0x1fff3000)
#define RAM_BEGIN      UINT32_C(0x20000000)
#define RAM_END        UINT32_C(0x20004000)
#define FALLBACK_HZ    UINT32_C(48000000)
#define MAX_HCLK_HZ    UINT32_C(72000000)

#ifdef K10_HSE_HZ
#define HSE_HZ ((uint32_t)K10_HSE_HZ)
#define HSE_IDENTIFIED true
#else
/* A constant used by the stock application, not a measured board crystal. */
#define HSE_HZ UINT32_C(16000000)
#define HSE_IDENTIFIED false
#endif

#ifdef K10_LSE_HZ
#define LSE_HZ ((uint32_t)K10_LSE_HZ)
#define LSE_IDENTIFIED true
#else
#define LSE_HZ UINT32_C(32768)
#define LSE_IDENTIFIED false
#endif

static volatile uint32_t milliseconds;
static uint32_t core_hz;
static uint32_t tick_period;
static uint32_t tick_ms;
static bool clock_known;
static bool initialized;

volatile struct hal_fault_record g_hal_fault
    __attribute__((section(".noinit.hal_fault"), aligned(4)));

extern unsigned char _fault_stack_bottom[];
extern unsigned char _fault_stack_top[];

struct decoded_clock {
    uint32_t hz;
    bool known;
};

/* Pure register decoder: no oscillator, divider or flash-latency writes. */
static struct decoded_clock decode_clock(uint32_t cr, uint32_t icscr,
                                        uint32_t cfgr, uint32_t pllcfgr)
{
    struct decoded_clock result = { FALLBACK_HZ, false };
    uint32_t hsi_hz = 0;
    uint32_t source_hz;
    bool known = true;

    switch ((icscr >> 13) & 7u) {
    case 0: hsi_hz = 4000000u; break;
    case 1: hsi_hz = 8000000u; break;
    case 2: hsi_hz = 16000000u; break;
    case 3: hsi_hz = 22120000u; break;
    case 4: hsi_hz = 24000000u; break;
    default: break; /* Reserved selections are not guessed to be 4 MHz. */
    }

    switch ((cfgr >> 3) & 7u) {
    case 0: /* HSI after HSIDIV. */
        if ((cr & (1u << 10)) == 0u || hsi_hz == 0u)
            return result;
        source_hz = hsi_hz >> ((cr >> 11) & 7u);
        break;
    case 1: /* HSE. */
        if ((cr & (1u << 17)) == 0u)
            return result;
        source_hz = HSE_HZ;
        known = HSE_IDENTIFIED;
        break;
    case 2: { /* PLL input is before the HSI system-clock divider. */
        uint32_t input = pllcfgr & 3u;
        uint32_t multiplier = (pllcfgr >> 2) & 3u;
        if ((cr & (1u << 25)) == 0u || multiplier > 1u)
            return result;
        if (input == 2u) {
            if ((cr & (1u << 10)) == 0u || hsi_hz == 0u)
                return result;
            source_hz = hsi_hz;
        } else if (input == 3u) {
            if ((cr & (1u << 17)) == 0u)
                return result;
            source_hz = HSE_HZ;
            known = HSE_IDENTIFIED;
        } else {
            return result;
        }
        /* Check before multiplying, including caller-supplied HSE values. */
        if (source_hz > MAX_HCLK_HZ / (multiplier + 2u))
            return result;
        source_hz *= multiplier + 2u;
        break;
    }
    case 3: /* The LSI frequency is nominal, not a calibration measurement. */
        source_hz = 32768u;
        break;
    case 4:
        source_hz = LSE_HZ;
        known = LSE_IDENTIFIED;
        break;
    default:
        return result;
    }

    /* HPRE 0..7: /1; 8..11: /2,/4,/8,/16; 12..15: /64../512. */
    uint32_t hpre = (cfgr >> 8) & 15u;
    uint32_t shift = 0u;
    if (hpre >= 8u)
        shift = (hpre < 12u) ? hpre - 7u : hpre - 6u;
    source_hz >>= shift;

    /* Extremely slow inherited clocks cannot support the diagnostic runtime. */
    if (source_hz < 1000u || source_hz > MAX_HCLK_HZ)
        return result;
    result.hz = source_hz;
    result.known = known;
    return result;
}

void hal_early_init(void)
{
    if (initialized)
        return;

    uint32_t cr = mmio_read32(RCC_CR);
    uint32_t icscr = mmio_read32(RCC_ICSCR);
    uint32_t cfgr = mmio_read32(RCC_CFGR);
    uint32_t pllcfgr = mmio_read32(RCC_PLLCFGR);
    struct decoded_clock decoded = decode_clock(cr, icscr, cfgr, pllcfgr);

    /* A clock switch in progress during the snapshot is not a known rate. */
    if (((cfgr ^ mmio_read32(RCC_CFGR)) & 0x0f38u) != 0u) {
        decoded.hz = FALLBACK_HZ;
        decoded.known = false;
    }
    core_hz = decoded.hz;
    clock_known = decoded.known;

    /* Usually 1 kHz; reduce IRQ cost if a low-speed clock was inherited. */
    tick_ms = core_hz < 100000u ? 100u : (core_hz < 1000000u ? 10u : 1u);
    tick_period = (core_hz * tick_ms + 500u) / 1000u;
    milliseconds = 0u;
    mmio_write32(SYSTICK_CTRL, 0u);
    mmio_write32(SYSTICK_LOAD, tick_period - 1u);
    mmio_write32(SYSTICK_VAL, 0u);
    /* Two priority bits are implemented; SysTick gets the lowest priority. */
    mmio_modify32(SCB_SHPR3, UINT32_C(0xff000000), UINT32_C(0xc0000000));
    initialized = true;
    /* CLKSOURCE=processor clock, TICKINT=1, ENABLE=1. */
    mmio_write32(SYSTICK_CTRL, 7u);
}

void SysTick_Handler(void)
{
    milliseconds += tick_ms;
}

uint32_t hal_millis(void)
{
    return milliseconds;
}

uint32_t hal_clock_hz(void)
{
    return core_hz;
}

bool hal_clock_known(void)
{
    return clock_known;
}

/* SysTick polling also works with PRIMASK set or within an interrupt. */
static void wait_cycles(uint32_t remaining)
{
    uint32_t previous = mmio_read32(SYSTICK_VAL);
    while (remaining != 0u) {
        uint32_t now = mmio_read32(SYSTICK_VAL);
        uint32_t elapsed = previous >= now ? previous - now
                                           : previous + tick_period - now;
        if (elapsed >= remaining)
            return;
        remaining -= elapsed;
        previous = now;
    }
}

void hal_delay_us(uint32_t microseconds)
{
    /* Split the arithmetic to avoid 64-bit division and 32-bit overflow. */
    uint32_t whole = core_hz / 1000000u;
    uint32_t fraction = core_hz % 1000000u;
    while (microseconds != 0u) {
        uint32_t part = microseconds > 1000u ? 1000u : microseconds;
        uint32_t cycles = whole * part
            + (fraction * part + 999999u) / 1000000u;
        wait_cycles(cycles);
        microseconds -= part;
    }
}

void hal_delay_ms(uint32_t delay)
{
    while (delay != 0u) {
        hal_delay_us(1000u);
        --delay;
    }
}

void hal_read_uid(uint32_t out[4])
{
    for (uint32_t i = 0; i < 4u; ++i)
        out[i] = mmio_read32(UID_BASE + i * 4u);
}

void hal_idle(void)
{
    K10_HAL_CPU_INSTRUCTIONS("dsb\n\twfi");
}

const volatile struct hal_fault_record *hal_last_fault(void)
{
    return &g_hal_fault;
}

void hal_fault_trap(uint32_t exception_number, uint32_t exception_return,
                    const uint32_t *frame)
{
    K10_HAL_CPU_INSTRUCTIONS("cpsid i");
    mmio_write32(SYSTICK_CTRL, 0u);
    uintptr_t address = (uintptr_t)frame;
    bool exc_valid = exception_return == UINT32_C(0xfffffff1)
        || exception_return == UINT32_C(0xfffffff9)
        || exception_return == UINT32_C(0xfffffffd);
    bool frame_valid = exc_valid && (address & 3u) == 0u
        && address >= RAM_BEGIN && address <= RAM_END - 32u;
    /* A nested fault's frame may overlap this handler's emergency stack. */
    if (frame_valid && address < (uintptr_t)_fault_stack_top
        && address + 32u > (uintptr_t)_fault_stack_bottom)
        frame_valid = false;

    g_hal_fault.magic = 0u;
    g_hal_fault.exception_number = exception_number;
    g_hal_fault.exception_return = exception_return;
    g_hal_fault.stack_pointer = (uint32_t)address;
    g_hal_fault.frame_valid = frame_valid ? 1u : 0u;
    for (uint32_t i = 0; i < 8u; ++i)
        g_hal_fault.stacked[i] = frame_valid ? ((volatile const uint32_t *)frame)[i] : 0u;
    g_hal_fault.icsr = mmio_read32(SCB_ICSR);
    g_hal_fault.magic = HAL_FAULT_MAGIC;
    for (;;)
        hal_idle();
}
