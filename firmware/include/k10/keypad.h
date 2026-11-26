/* SPDX-License-Identifier: MIT */
#ifndef K10_KEYPAD_H
#define K10_KEYPAD_H

#include <stdbool.h>
#include <stdint.h>

#define KEYPAD_NONE UINT8_C(0xff)
#define KEYPAD_PTT UINT8_C(21)
#define KEYPAD_DEBOUNCE_MS UINT32_C(20)

enum keypad_error {
    KEYPAD_OK = 0,
    KEYPAD_DISABLED = 1,
    KEYPAD_NOT_INITIALIZED = 2,
    KEYPAD_INVALID_ARGUMENT = 3
};

enum keypad_event {
    KEYPAD_PRESS = 1u << 0,
    KEYPAD_RELEASE = 1u << 1,
    KEYPAD_PTT_PRESS = 1u << 2,
    KEYPAD_PTT_RELEASE = 1u << 3
};

struct keypad_state {
    uint8_t raw_key;
    uint8_t key;
    uint8_t previous_key; /* released key if KEYPAD_RELEASE is set */
    uint8_t events;
    bool raw_ptt;
    bool ptt;
};

/* Opt-in: K10_ENABLE_KEYPAD=1 takes PA14 from SWCLK. This API reports input
 * only; PTT never changes an RF output. Call from one non-interrupt context.
 * The independent diagnostic filter requires 20 ms of unchanged samples;
 * it does not implement the stock repeat/long-press/menu state machine. */
bool keypad_init(void);
bool keypad_poll(uint32_t now_ms, struct keypad_state *state);
enum keypad_error keypad_last_error(void);
const char *keypad_label(uint8_t code);

#endif
