/* SPDX-License-Identifier: MIT */
#ifndef K10_SI4732_H
#define K10_SI4732_H

#include <stdbool.h>
#include <stdint.h>

enum si4732_error {
    SI4732_OK = 0,
    SI4732_DISABLED,
    SI4732_NOT_INITIALIZED,
    SI4732_INVALID_ARGUMENT,
    SI4732_BUS_STUCK,
    SI4732_NACK,
    SI4732_TIMEOUT,
    SI4732_COMMAND_ERROR,
    SI4732_UNEXPECTED_PART,
    SI4732_TUNE_MISMATCH
};

struct si4732_revision {
    uint8_t part_number;
    uint8_t firmware_major;
    uint8_t firmware_minor;
    uint16_t patch_id;
    uint8_t component_major;
    uint8_t component_minor;
    uint8_t chip_revision;
};

/* PB8/PB9 open-drain I2C, address 0x11. Starts the on-chip FM receiver using
 * the observed POWER_UP arguments, waits for its crystal, and checks PN=0x20.
 * Does not guess reset/power/audio-switch pins or load an external patch.
 * The receiver must already be powered and released from hardware reset. */
bool si4732_init(void);
bool si4732_get_revision(struct si4732_revision *revision);

/* Optional explicit receive-only operation, 64.00--108.00 MHz in 10 kHz units.
 * Blocks until STC, acknowledges completion and verifies the readback.
 * No board speaker amplifier, antenna switch or transmit GPIO is touched. */
bool si4732_fm_tune(uint16_t frequency_10khz);

enum si4732_error si4732_last_error(void);
uint8_t si4732_last_status(void);

/* Single caller only: the command and bit-banged bus state is not reentrant. */

#endif
