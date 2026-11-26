/* SPDX-License-Identifier: MIT */
#ifndef K10_FRONTPANEL_H
#define K10_FRONTPANEL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

struct frontpanel_view {
    uint32_t clock_hz;
    uint32_t uptime_ms;
    uint32_t flags;
    uint32_t stage;
    uint32_t jedec_id;
    uint32_t flash_capacity;
    uint32_t flash_error;
    uint32_t receiver_error;
    uint32_t receiver_part;
    uint32_t display_error;
    uint32_t keypad_error;
    uint32_t key_events;
    uint8_t raw_key;
    uint8_t key;
    bool ptt;
};

/* Pure renderer, no MMIO. Writes exactly one 128x64, eight-page framebuffer.
 * Invalid pointers/length leave the destination unchanged. */
bool frontpanel_render(uint8_t *framebuffer, size_t length,
                       const struct frontpanel_view *view);

#endif
