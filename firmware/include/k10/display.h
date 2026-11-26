/* SPDX-License-Identifier: MIT */
#ifndef K10_DISPLAY_H
#define K10_DISPLAY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define DISPLAY_WIDTH 128u
#define DISPLAY_HEIGHT 64u
#define DISPLAY_FRAMEBUFFER_SIZE (DISPLAY_WIDTH * DISPLAY_HEIGHT / 8u)

enum display_error {
    DISPLAY_OK = 0,
    DISPLAY_DISABLED,
    DISPLAY_NOT_INITIALIZED,
    DISPLAY_INVALID_ARGUMENT,
    DISPLAY_TIMEOUT,
    DISPLAY_BUS_ERROR
};

/* Owns SPI1, the documented LCD GPIOs, and TIM1/2/3 for RGB PWM.
 * No LCD RESET/power pin is guessed. Initialization clears the LCD and leaves
 * all three backlight compare values at zero. Success confirms local SPI
 * transactions; the write-only panel provides no presence/identity check.
 * Call from one execution context, not concurrently or from an interrupt. */
bool display_init(void);

/* Exactly 1024 bytes: page * 128 + x, eight vertical pixels per byte.
 * The buffer remains caller-owned and is never modified or retained.
 * A bus fault releases CS, turns backlight PWM off and requires reinit. */
bool display_present(const uint8_t *framebuffer, size_t length);

/* Accepts 0..100 per channel; invalid values leave current compare values
 * unchanged. The stock scale is compare = value * 25, ARR = 2500.
 * Requires successful display_init(), including after a bus fault. */
bool display_set_backlight(uint8_t r, uint8_t g, uint8_t b);
enum display_error display_last_error(void);

#endif
