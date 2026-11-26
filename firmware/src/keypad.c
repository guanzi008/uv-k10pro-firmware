/* SPDX-License-Identifier: MIT */
#include "k10/board.h"
#include "k10/hal.h"
#include "k10/keypad.h"
#include "k10/mmio.h"

#include <stddef.h>

static enum keypad_error last_error = KEYPAD_NOT_INITIALIZED;

#if K10_ENABLE_KEYPAD
#define GPIO_MODER 0x00u
#define GPIO_OTYPER 0x04u
#define GPIO_SPEED 0x08u
#define GPIO_PULL 0x0cu
#define GPIO_IDR 0x10u
#define GPIO_BSRR 0x18u
#define GPIO_BRR 0x28u

struct pin { uintptr_t port; uint8_t bit; };
static const struct pin rows[] = {
    {K10_GPIOF_BASE, K10_KEY_ROW0_PIN},
    {K10_GPIOB_BASE, K10_KEY_ROW1_PIN},
    {K10_GPIOB_BASE, K10_KEY_ROW2_PIN},
    {K10_GPIOA_BASE, K10_KEY_ROW3_PIN}
};
static const uint8_t keymap[4][5] = {
    {17u, 1u, 4u, 7u, 10u},
    {12u, 2u, 5u, 8u, 11u},
    {13u, 3u, 6u, 9u, KEYPAD_NONE},
    {18u, 14u, 0u, 15u, 16u}
};
struct input_filter {
    uint32_t changed_at;
    uint8_t candidate;
    uint8_t stable;
};
static struct input_filter key_filter;
static struct input_filter ptt_filter;
static bool initialized;

static void all_rows_high(void)
{
    for (size_t i = 0u; i < 4u; ++i) {
        mmio_write32(rows[i].port + GPIO_BSRR, UINT32_C(1) << rows[i].bit);
    }
}

static void input_pin(uintptr_t port, uint32_t pin)
{
    uint32_t shift = pin * 2u;
    mmio_modify32(port + GPIO_MODER, UINT32_C(3) << shift, 0u);
    mmio_modify32(port + GPIO_PULL, UINT32_C(3) << shift, UINT32_C(1) << shift);
}

static void output_pin(const struct pin *pin)
{
    uint32_t shift = (uint32_t)pin->bit * 2u;
    /* Stock rows are push-pull, pull-up, speed 3. Do not configure the
     * unrelated PA13 output from the stock whole-board initializer. */
    mmio_modify32(pin->port + GPIO_OTYPER, UINT32_C(1) << pin->bit, 0u);
    mmio_modify32(pin->port + GPIO_SPEED, UINT32_C(3) << shift, UINT32_C(3) << shift);
    mmio_modify32(pin->port + GPIO_PULL, UINT32_C(3) << shift, UINT32_C(1) << shift);
    mmio_modify32(pin->port + GPIO_MODER, UINT32_C(3) << shift, UINT32_C(1) << shift);
}

static uint8_t scan_key(void)
{
    all_rows_high();
    uint32_t direct = mmio_read32(K10_GPIOB_BASE + GPIO_IDR);
    if ((direct & (UINT32_C(1) << 15)) == 0u) { return 24u; }
    if ((direct & (UINT32_C(1) << 14)) == 0u) { return 23u; }
    if ((direct & (UINT32_C(1) << 13)) == 0u) { return 25u; }

    for (size_t row = 0u; row < 4u; ++row) {
        mmio_write32(rows[row].port + GPIO_BRR, UINT32_C(1) << rows[row].bit);
        hal_delay_us(1u);
        uint32_t pb = mmio_read32(K10_GPIOB_BASE + GPIO_IDR);
        uint32_t pa = mmio_read32(K10_GPIOA_BASE + GPIO_IDR);
        /* Restore before inspecting or returning, including the absent cell. */
        mmio_write32(rows[row].port + GPIO_BSRR, UINT32_C(1) << rows[row].bit);
        hal_delay_us(1u);
        for (size_t col = 0u; col < 5u; ++col) {
            bool low = col < 4u ? ((pb & (UINT32_C(1) << (15u - col))) == 0u)
                                : ((pa & (UINT32_C(1) << K10_KEY_COLUMN_A_PIN)) == 0u);
            if (low && keymap[row][col] != KEYPAD_NONE) {
                return keymap[row][col];
            }
        }
    }
    return KEYPAD_NONE;
}

static bool filter_changed(struct input_filter *filter, uint8_t value, uint32_t now)
{
    if (value != filter->candidate) {
        filter->candidate = value;
        filter->changed_at = now;
    } else if (value != filter->stable &&
               (uint32_t)(now - filter->changed_at) >= KEYPAD_DEBOUNCE_MS) {
        filter->stable = value;
        return true;
    }
    return false;
}
#endif

bool keypad_init(void)
{
#if K10_ENABLE_KEYPAD
    mmio_modify32(K10_RCC_BASE + 0x34u, 0u, UINT32_C(0x27)); /* GPIO A B C F */
    (void)mmio_read32(K10_RCC_BASE + 0x34u);
    all_rows_high();
    for (size_t i = 0u; i < 4u; ++i) { output_pin(&rows[i]); }
    for (uint32_t pin = 12u; pin <= 15u; ++pin) { input_pin(K10_GPIOB_BASE, pin); }
    input_pin(K10_GPIOA_BASE, K10_KEY_COLUMN_A_PIN);
    input_pin(K10_GPIOC_BASE, K10_KEY_PTT_PIN);
    key_filter = (struct input_filter){0u, KEYPAD_NONE, KEYPAD_NONE};
    ptt_filter = (struct input_filter){0u, 0u, 0u};
    initialized = true;
    last_error = KEYPAD_OK;
    return true;
#else
    last_error = KEYPAD_DISABLED;
    return false;
#endif
}

bool keypad_poll(uint32_t now_ms, struct keypad_state *state)
{
#if K10_ENABLE_KEYPAD
    if (state == NULL) {
        last_error = KEYPAD_INVALID_ARGUMENT;
        return false;
    }
    if (!initialized) {
        last_error = KEYPAD_NOT_INITIALIZED;
        return false;
    }
    uint8_t raw = scan_key();
    bool ptt = (mmio_read32(K10_GPIOC_BASE + GPIO_IDR) &
                (UINT32_C(1) << K10_KEY_PTT_PIN)) == 0u;
    uint8_t previous = key_filter.stable;
    uint8_t events = 0u;
    if (filter_changed(&key_filter, raw, now_ms)) {
        if (previous != KEYPAD_NONE) { events |= KEYPAD_RELEASE; }
        if (key_filter.stable != KEYPAD_NONE) { events |= KEYPAD_PRESS; }
    }
    if (filter_changed(&ptt_filter, ptt ? 1u : 0u, now_ms)) {
        events |= ptt ? KEYPAD_PTT_PRESS : KEYPAD_PTT_RELEASE;
    }
    *state = (struct keypad_state){
        raw, key_filter.stable, previous, events, ptt, ptt_filter.stable != 0u
    };
    last_error = KEYPAD_OK;
    return true;
#else
    (void)now_ms;
    (void)state;
    last_error = KEYPAD_DISABLED;
    return false;
#endif
}

enum keypad_error keypad_last_error(void) { return last_error; }

const char *keypad_label(uint8_t code)
{
    static const char *const labels[] = {
        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
        "A/B", "KEY11", "UP", "DOWN", "KEY14", "LOCK", "KEY16", "MENU", "EXIT"
    };
    if (code < sizeof(labels) / sizeof(labels[0])) { return labels[code]; }
    switch (code) {
    case KEYPAD_NONE: return "NONE";
    case KEYPAD_PTT: return "PTT";
    case 23u: return "SK2";
    case 24u: return "SK1";
    case 25u: return "SK3";
    default: return "UNKNOWN";
    }
}
