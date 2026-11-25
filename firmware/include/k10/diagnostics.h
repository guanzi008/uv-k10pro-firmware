/* SPDX-License-Identifier: MIT */
#ifndef K10_DIAGNOSTICS_H
#define K10_DIAGNOSTICS_H

#include <stdint.h>

#define K10_DIAG_MAGIC 0x4430314bu /* little-endian "K10D" */
#define K10_DIAG_ABI 1u
#define K10_DIAG_BUFFER_SIZE 64u

enum k10_stage {
    K10_STAGE_START = 1,
    K10_STAGE_SPI = 2,
    K10_STAGE_RECEIVER = 3,
    K10_STAGE_READY = 4
};

enum k10_diag_flag {
    K10_FLAG_CLOCK_KNOWN = 1u << 0,
    K10_FLAG_JEDEC_READ = 1u << 1,
    K10_FLAG_FLASH_READ = 1u << 2,
    K10_FLAG_RECEIVER_REVISION = 1u << 3
};

enum k10_command {
    K10_COMMAND_NONE = 0,
    K10_COMMAND_PROBE_FLASH = 1,
    K10_COMMAND_READ_FLASH = 2,
    K10_COMMAND_PROBE_RECEIVER = 3,
    K10_COMMAND_TUNE_FM = 4
};

enum k10_result {
    K10_RESULT_OK = 0,
    K10_RESULT_BAD_COMMAND = 1,
    K10_RESULT_BAD_ARGUMENT = 2,
    K10_RESULT_DRIVER_ERROR = 3,
    K10_RESULT_NOT_READY = 4
};

/* All scalar fields are uint32_t for a stable, debugger-friendly ABI.
 * Halt the target to read a consistent snapshot. Write arguments, command,
 * then request_seq last; resume, wait, and halt again. completion_seq ==
 * request_seq means result and buffer have been committed. One producer and
 * at most one outstanding request: require stage == READY and equal sequence
 * fields before posting; choose a new request_seq != completion_seq. Do not
 * overwrite a pending command. Never write status fields while running.
 */
struct k10_diagnostics {
    uint32_t magic;
    uint32_t abi;
    uint32_t struct_bytes;
    uint32_t stage;
    uint32_t flags;
    uint32_t clock_hz;
    uint32_t uptime_ms;
    uint32_t heartbeat;
    uint32_t uid[4];
    uint32_t flash_capacity_bytes;
    uint32_t flash_error;
    uint32_t jedec_id; /* manufacturer << 16 | memory type << 8 | capacity */
    uint32_t receiver_error;
    uint32_t receiver_status;
    uint32_t receiver_part_number;
    uint32_t receiver_firmware; /* major byte << 8 | minor byte */
    uint32_t receiver_patch_id;
    uint32_t receiver_component; /* major byte << 8 | minor byte */
    uint32_t receiver_chip_revision;
    uint32_t tuned_frequency_10khz;
    uint32_t buffer_length;
    uint32_t buffer_address;
    uint32_t request_seq;
    uint32_t completion_seq;
    uint32_t command;
    uint32_t argument0;
    uint32_t argument1;
    uint32_t result;
    uint8_t buffer[K10_DIAG_BUFFER_SIZE];
};

extern volatile struct k10_diagnostics k10_diag;

#endif
