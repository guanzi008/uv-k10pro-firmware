/* SPDX-License-Identifier: MIT */
#ifndef K10_HAL_H
#define K10_HAL_H

#include <stdbool.h>
#include <stdint.h>

/* startup.S calls this after .data/.bss initialization, with IRQs masked. */
void hal_early_init(void);

/*
 * Wraps modulo 2^32. Use unsigned subtraction for elapsed-time checks.
 * Tick resolution is 1 ms at >= 1 MHz, 10 ms below, 100 ms below 100 kHz.
 * Polling delays do not depend on interrupt delivery; long IRQs extend them.
 */
uint32_t hal_millis(void);
void hal_delay_ms(uint32_t milliseconds);
void hal_delay_us(uint32_t microseconds);

/*
 * Nominal HCLK used for timing; the oscillator has not been measured.
 * Clock registers are inspected, never reprogrammed. Unknown settings use a
 * clearly flagged 48 MHz estimate. HSE defaults to the stock app's 16 MHz
 * assumption and is also flagged as unknown. Define K10_HSE_HZ only after
 * identifying the actual board oscillator. K10_LSE_HZ has the same contract.
 */
uint32_t hal_clock_hz(void);
bool hal_clock_known(void);

/* Read the four 32-bit words of the PY32F071 unique identifier. */
void hal_read_uid(uint32_t out[4]);
void hal_idle(void);

#define HAL_FAULT_MAGIC UINT32_C(0x4b313046)

/*
 * Available to an attached debugger after an exception. .noinit is preserved
 * by our startup, but preservation through the vendor bootloader is unknown.
 * stacked[] order: r0, r1, r2, r3, r12, lr, pc, xPSR. A valid magic with
 * frame_valid == 0 means the interrupted stack could not safely be read.
 */
struct hal_fault_record {
    uint32_t magic;
    uint32_t exception_number;
    uint32_t exception_return;
    uint32_t stack_pointer;
    uint32_t frame_valid;
    uint32_t stacked[8];
    uint32_t icsr;
};

extern volatile struct hal_fault_record g_hal_fault;
const volatile struct hal_fault_record *hal_last_fault(void);

/* Internal entry from startup.S; never returns or attempts a radio action. */
__attribute__((noreturn))
void hal_fault_trap(uint32_t exception_number, uint32_t exception_return,
                    const uint32_t *frame);

#endif
