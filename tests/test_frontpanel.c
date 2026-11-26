/* SPDX-License-Identifier: MIT */
#include "k10/board.h"
#include "k10/diagnostics.h"
#include "k10/frontpanel.h"
#include "k10/keypad.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

static struct frontpanel_view example(void)
{
    return (struct frontpanel_view){
        .clock_hz = 48000000u, .uptime_ms = 123000u,
        .flags = K10_FLAG_CLOCK_KNOWN | K10_FLAG_JEDEC_READ | K10_FLAG_RECEIVER_REVISION,
        .stage = K10_STAGE_READY, .jedec_id = 0xef4016u,
        .flash_capacity = K10_NOR_EXPECTED_CAPACITY_BYTES,
        .receiver_part = 0x20u, .keypad_error = KEYPAD_OK,
        .key_events = 7u, .raw_key = 17u, .key = 17u, .ptt = false
    };
}

static void bounds(void)
{
    struct { uint8_t before[16]; uint8_t buffer[1024]; uint8_t after[16]; } memory;
    memset(&memory, 0xa5, sizeof(memory));
    struct frontpanel_view view = example();
    assert(!frontpanel_render(NULL, 1024u, &view));
    assert(!frontpanel_render(memory.buffer, 1024u, NULL));
    assert(!frontpanel_render(memory.buffer, 1023u, &view));
    assert(!frontpanel_render(memory.buffer, 1025u, &view));
    for (size_t i = 0u; i < sizeof(memory.buffer); ++i) { assert(memory.buffer[i] == 0xa5u); }
    view.clock_hz = view.uptime_ms = view.flash_error = view.receiver_error = UINT32_MAX;
    view.display_error = view.keypad_error = view.key_events = UINT32_MAX;
    assert(frontpanel_render(memory.buffer, sizeof(memory.buffer), &view));
    for (size_t i = 0u; i < sizeof(memory.before); ++i) {
        assert(memory.before[i] == 0xa5u && memory.after[i] == 0xa5u);
    }
    for (unsigned x = 0u; x < 128u; ++x) { assert((memory.buffer[x] & 0x80u) != 0u); }
}

static void states(void)
{
    uint8_t baseline[1024], first[1024], second[1024];
    struct frontpanel_view view = example();
    assert(frontpanel_render(baseline, sizeof(baseline), &view));
    /* A disabled input must not show false key/PTT values from stale fields. */
    view.keypad_error = KEYPAD_DISABLED;
    view.key = view.raw_key = 0u; view.ptt = true;
    assert(frontpanel_render(first, sizeof(first), &view));
    view.key = view.raw_key = 9u; view.ptt = false;
    assert(frontpanel_render(second, sizeof(second), &view));
    assert(memcmp(first + 5u * 128u, second + 5u * 128u, 256u) == 0);
    assert(memcmp(baseline + 5u * 128u, first + 5u * 128u, 256u) != 0);
    view = example();
    view.flags &= ~(uint32_t)K10_FLAG_JEDEC_READ;
    view.jedec_id = 0u;
    assert(frontpanel_render(first, sizeof(first), &view));
    view.jedec_id = UINT32_MAX;
    assert(frontpanel_render(second, sizeof(second), &view));
    assert(memcmp(first + 2u * 128u, second + 2u * 128u, 128u) == 0);
    view = example(); view.flash_capacity = 0u;
    assert(frontpanel_render(first, sizeof(first), &view));
    view.flash_capacity = 2097152u;
    assert(frontpanel_render(second, sizeof(second), &view));
    assert(memcmp(first + 3u * 128u, second + 3u * 128u, 128u) != 0);
    assert(memcmp(first + 3u * 128u, baseline + 3u * 128u, 128u) != 0);
    view = example(); view.raw_key = view.key = KEYPAD_NONE;
    assert(frontpanel_render(first, sizeof(first), &view));
    view.raw_key = view.key = 0u;
    assert(frontpanel_render(second, sizeof(second), &view));
    assert(memcmp(first + 5u * 128u, second + 5u * 128u, 128u) != 0);
    view = example(); view.stage = K10_STAGE_FRONTPANEL;
    assert(frontpanel_render(first, sizeof(first), &view));
    assert(memcmp(first + 7u * 128u, baseline + 7u * 128u, 128u) != 0);
}

int main(int argc, char **argv)
{
    assert(argc == 2);
    if (strcmp(argv[1], "bounds") == 0) { bounds(); }
    else if (strcmp(argv[1], "states") == 0) { states(); }
    else if (strcmp(argv[1], "preview") == 0) {
        uint8_t framebuffer[1024];
        struct frontpanel_view view = example();
        assert(frontpanel_render(framebuffer, sizeof(framebuffer), &view));
        assert(fwrite(framebuffer, 1u, sizeof(framebuffer), stdout) == sizeof(framebuffer));
        return 0;
    } else { assert(!"unknown test"); }
    printf("PASS: frontpanel %s\n", argv[1]);
    return 0;
}
