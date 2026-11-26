/* SPDX-License-Identifier: MIT */
#include "k10/board.h"
#include "k10/diagnostics.h"
#include "k10/display.h"
#include "k10/frontpanel.h"
#include "k10/hal.h"
#include "k10/keypad.h"
#include "k10/si4732.h"
#include "k10/spi_nor.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifndef K10_VERSION
#define K10_VERSION "dev"
#endif

volatile struct k10_diagnostics k10_diag;
const char k10_firmware_version[] __attribute__((used, section(".firmware_info"))) = K10_VERSION;

static bool receiver_ready;
static bool display_ready;
static bool keypad_ready;
/* Dedicated storage: the stock application shares its LCD buffer and NOR
 * sector cache; this application deliberately has separate ownership. */
static uint8_t framebuffer[DISPLAY_FRAMEBUFFER_SIZE];

static void publish_barrier(void)
{
    __asm volatile("dmb" ::: "memory");
}

static uint32_t read_flash(uint32_t address, uint32_t length)
{
    uint8_t data[K10_DIAG_BUFFER_SIZE];
    k10_diag.buffer_length = 0u;
    k10_diag.flags &= ~(uint32_t)K10_FLAG_FLASH_READ;
    if (length == 0u || length > K10_DIAG_BUFFER_SIZE) {
        return K10_RESULT_BAD_ARGUMENT;
    }
    if (!spi_nor_read(address, data, (size_t)length)) {
        k10_diag.flash_error = (uint32_t)spi_nor_last_error();
        return K10_RESULT_DRIVER_ERROR;
    }
    for (uint32_t i = 0u; i < length; ++i) {
        k10_diag.buffer[i] = data[i];
    }
    k10_diag.buffer_address = address;
    k10_diag.buffer_length = length;
    k10_diag.flash_error = (uint32_t)spi_nor_last_error();
    k10_diag.flags |= K10_FLAG_FLASH_READ;
    return K10_RESULT_OK;
}

static uint32_t probe_flash(void)
{
    uint8_t id[3] = {0u, 0u, 0u};
    k10_diag.flags &= ~((uint32_t)K10_FLAG_JEDEC_READ | (uint32_t)K10_FLAG_FLASH_READ |
                        (uint32_t)K10_FLAG_FLASH_GEOMETRY_MATCH);
    k10_diag.jedec_id = 0u;
    k10_diag.flash_capacity_bytes = 0u;
    k10_diag.buffer_length = 0u;
    if (!spi_nor_init() || !spi_nor_read_jedec(id)) {
        k10_diag.flash_error = (uint32_t)spi_nor_last_error();
        return K10_RESULT_DRIVER_ERROR;
    }
    k10_diag.jedec_id = ((uint32_t)id[0] << 16) | ((uint32_t)id[1] << 8) | id[2];
    k10_diag.flash_capacity_bytes = spi_nor_capacity_bytes();
    k10_diag.flash_error = (uint32_t)spi_nor_last_error();
    k10_diag.flags |= K10_FLAG_JEDEC_READ;
    if (k10_diag.flash_capacity_bytes == K10_NOR_EXPECTED_CAPACITY_BYTES) {
        k10_diag.flags |= K10_FLAG_FLASH_GEOMETRY_MATCH;
    }
    if (k10_diag.flash_capacity_bytes != 0u) {
        return read_flash(0u, K10_DIAG_BUFFER_SIZE);
    }
    /* Raw JEDEC identification succeeded; capacity is deliberately unknown. */
    return K10_RESULT_OK;
}

static uint32_t probe_receiver(void)
{
    struct si4732_revision revision;
    receiver_ready = false;
    k10_diag.flags &= ~(uint32_t)K10_FLAG_RECEIVER_REVISION;
    k10_diag.receiver_part_number = 0u;
    k10_diag.receiver_firmware = 0u;
    k10_diag.receiver_patch_id = 0u;
    k10_diag.receiver_component = 0u;
    k10_diag.receiver_chip_revision = 0u;
    k10_diag.tuned_frequency_10khz = 0u;
    bool initialized = si4732_init();
    if (!initialized && si4732_last_error() != SI4732_UNEXPECTED_PART) {
        k10_diag.receiver_error = (uint32_t)si4732_last_error();
        k10_diag.receiver_status = si4732_last_status();
        return K10_RESULT_DRIVER_ERROR;
    }
    bool supported = si4732_get_revision(&revision);
    if (!supported && si4732_last_error() != SI4732_UNEXPECTED_PART) {
        k10_diag.receiver_error = (uint32_t)si4732_last_error();
        k10_diag.receiver_status = si4732_last_status();
        return K10_RESULT_DRIVER_ERROR;
    }
    k10_diag.receiver_part_number = revision.part_number;
    k10_diag.receiver_firmware = ((uint32_t)revision.firmware_major << 8) | revision.firmware_minor;
    k10_diag.receiver_patch_id = revision.patch_id;
    k10_diag.receiver_component = ((uint32_t)revision.component_major << 8) | revision.component_minor;
    k10_diag.receiver_chip_revision = revision.chip_revision;
    k10_diag.receiver_error = (uint32_t)si4732_last_error();
    k10_diag.receiver_status = si4732_last_status();
    k10_diag.flags |= K10_FLAG_RECEIVER_REVISION;
    /* Preserve a well-formed revision from an unexpected part for diagnosis,
     * but never permit this driver's tune path until PN=0x20 is confirmed. */
    receiver_ready = supported;
    return supported ? K10_RESULT_OK : K10_RESULT_DRIVER_ERROR;
}

static uint32_t execute_command(uint32_t command, uint32_t argument0, uint32_t argument1)
{
    switch (command) {
    case K10_COMMAND_NONE:
        return K10_RESULT_OK;
    case K10_COMMAND_PROBE_FLASH:
        return probe_flash();
    case K10_COMMAND_READ_FLASH:
        return read_flash(argument0, argument1);
    case K10_COMMAND_PROBE_RECEIVER:
        return probe_receiver();
    case K10_COMMAND_TUNE_FM:
        if (argument0 < 6400u || argument0 > 10800u) {
            return K10_RESULT_BAD_ARGUMENT;
        }
        if (!receiver_ready) {
            return K10_RESULT_NOT_READY;
        }
        /* A timed-out command may already have retuned the receiver. */
        k10_diag.tuned_frequency_10khz = 0u;
        if (!si4732_fm_tune((uint16_t)argument0)) {
            k10_diag.receiver_error = (uint32_t)si4732_last_error();
            k10_diag.receiver_status = si4732_last_status();
            return K10_RESULT_DRIVER_ERROR;
        }
        k10_diag.tuned_frequency_10khz = argument0;
        k10_diag.receiver_error = (uint32_t)si4732_last_error();
        k10_diag.receiver_status = si4732_last_status();
        return K10_RESULT_OK;
    default:
        return K10_RESULT_BAD_COMMAND;
    }
}

static void refresh_frontpanel(void)
{
    if (!display_ready) { return; }
    const struct frontpanel_view view = {
        .clock_hz = k10_diag.clock_hz,
        .uptime_ms = k10_diag.uptime_ms,
        .flags = k10_diag.flags,
        .stage = k10_diag.stage,
        .jedec_id = k10_diag.jedec_id,
        .flash_capacity = k10_diag.flash_capacity_bytes,
        .flash_error = k10_diag.flash_error,
        .receiver_error = k10_diag.receiver_error,
        .receiver_part = k10_diag.receiver_part_number,
        .display_error = k10_diag.display_error,
        .keypad_error = k10_diag.keypad_error,
        .key_events = k10_diag.key_event_count,
        .raw_key = (uint8_t)k10_diag.key_raw,
        .key = (uint8_t)k10_diag.key_stable,
        .ptt = k10_diag.ptt_stable != 0u
    };
    if (!frontpanel_render(framebuffer, sizeof(framebuffer), &view)) { return; }
    if (display_present(framebuffer, sizeof(framebuffer))) {
        ++k10_diag.display_frames;
    } else {
        display_ready = false;
        k10_diag.flags &= ~(uint32_t)K10_FLAG_DISPLAY_READY;
    }
    k10_diag.display_error = (uint32_t)display_last_error();
}

static void poll_frontpanel(uint32_t now)
{
    struct keypad_state state;
    if (!keypad_ready) { return; }
    if (keypad_poll(now, &state)) {
        k10_diag.key_raw = state.raw_key;
        k10_diag.key_stable = state.key;
        k10_diag.ptt_raw = state.raw_ptt ? 1u : 0u;
        k10_diag.ptt_stable = state.ptt ? 1u : 0u;
        if (state.events != 0u) {
            k10_diag.key_last_events = state.events;
            k10_diag.key_last_released = (state.events & KEYPAD_RELEASE) != 0u
                                           ? state.previous_key : KEYPAD_NONE;
            ++k10_diag.key_event_count;
        }
    } else {
        keypad_ready = false;
        k10_diag.flags &= ~(uint32_t)K10_FLAG_KEYPAD_READY;
    }
    k10_diag.keypad_error = (uint32_t)keypad_last_error();
}

int main(void)
{
    uint32_t uid[4];
    /* startup.S has already initialized RAM, vectors, and the HAL once. */
    k10_diag.abi = K10_DIAG_ABI;
    k10_diag.struct_bytes = (uint32_t)sizeof(k10_diag);
    k10_diag.stage = K10_STAGE_START;
    k10_diag.clock_hz = hal_clock_hz();
    k10_diag.flags = hal_clock_known() ? K10_FLAG_CLOCK_KNOWN : 0u;
    k10_diag.expected_flash_capacity_bytes = K10_NOR_EXPECTED_CAPACITY_BYTES;
    k10_diag.key_raw = KEYPAD_NONE;
    k10_diag.key_stable = KEYPAD_NONE;
    k10_diag.key_last_released = KEYPAD_NONE;
    hal_read_uid(uid);
    for (uint32_t i = 0u; i < 4u; ++i) {
        k10_diag.uid[i] = uid[i];
    }
    publish_barrier();
    k10_diag.magic = K10_DIAG_MAGIC;

    k10_diag.stage = K10_STAGE_FRONTPANEL;
    display_ready = display_init();
    if (display_ready) { display_ready = display_set_backlight(25u, 25u, 25u); }
    k10_diag.display_error = (uint32_t)display_last_error();
    if (display_ready) { k10_diag.flags |= K10_FLAG_DISPLAY_READY; }
    keypad_ready = keypad_init();
    k10_diag.keypad_error = (uint32_t)keypad_last_error();
    if (keypad_ready) { k10_diag.flags |= K10_FLAG_KEYPAD_READY; }
    refresh_frontpanel();

    k10_diag.stage = K10_STAGE_SPI;
    (void)probe_flash();
    refresh_frontpanel();
    k10_diag.stage = K10_STAGE_RECEIVER;
    (void)probe_receiver();
    k10_diag.stage = K10_STAGE_READY;
    refresh_frontpanel();

    uint32_t last_poll = hal_millis();
    uint32_t last_refresh = last_poll;
    for (;;) {
        k10_diag.uptime_ms = hal_millis();
        ++k10_diag.heartbeat;
        uint32_t sequence = k10_diag.request_seq;
        if (sequence != k10_diag.completion_seq) {
            publish_barrier();
            uint32_t command = k10_diag.command;
            uint32_t arg0 = k10_diag.argument0;
            uint32_t arg1 = k10_diag.argument1;
            k10_diag.result = execute_command(command, arg0, arg1);
            publish_barrier();
            k10_diag.completion_seq = sequence;
        }
        uint32_t now = hal_millis();
        if ((uint32_t)(now - last_poll) >= 10u) {
            last_poll = now;
            poll_frontpanel(now);
        }
        if ((uint32_t)(now - last_refresh) >= 250u) {
            last_refresh = now;
            refresh_frontpanel();
        }
        hal_idle();
    }
}
