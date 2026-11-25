/* SPDX-License-Identifier: MIT
 * Independent Si4732 command driver; board evidence and primary references
 * are recorded in docs/pinmap.md. No patch or vendor implementation included. */
#include "k10/si4732.h"
#include "k10/board.h"
#include "k10/hal.h"
#include "k10/mmio.h"
#include <stddef.h>

static enum si4732_error last_error = SI4732_NOT_INITIALIZED;
static uint8_t last_status;

#if K10_ENABLE_SI4732

#define GPIO_MODER       0x00u
#define GPIO_OTYPER      0x04u
#define GPIO_OSPEEDR     0x08u
#define GPIO_PUPDR       0x0cu
#define GPIO_IDR         0x10u
#define GPIO_BSRR        0x18u
#define GPIO_BRR         0x28u
#define RCC_IOPENR       0x34u
#define SCL_MASK         (UINT32_C(1) << K10_SI4732_SCL_PIN)
#define SDA_MASK         (UINT32_C(1) << K10_SI4732_SDA_PIN)
#define BUS_MASK         (SCL_MASK | SDA_MASK)
#define BUS_MODE_MASK    ((UINT32_C(3) << (2u * K10_SI4732_SCL_PIN)) | \
                         (UINT32_C(3) << (2u * K10_SI4732_SDA_PIN)))
#define BUS_OUTPUT_MODE  ((UINT32_C(1) << (2u * K10_SI4732_SCL_PIN)) | \
                         (UINT32_C(1) << (2u * K10_SI4732_SDA_PIN)))
#define HALF_PERIOD_US   10u
#define CTS              UINT8_C(0x80)
#define COMMAND_ERR      UINT8_C(0x40)
#define STC              UINT8_C(0x01)
#define COMMAND_WAIT_MS  1000u

static bool bus_ready;
static bool powered_up;
static bool part_confirmed;

static bool fail(enum si4732_error error)
{
    last_error = error;
    return false;
}

/* With OTYPER set, a high output latch releases the line. These helpers
 * never drive a push-pull high against an acknowledging receiver. */
static void drive_low(uint32_t mask)
{
    mmio_write32(K10_GPIOB_BASE + GPIO_BRR, mask);
}

static void release_line(uint32_t mask)
{
    mmio_write32(K10_GPIOB_BASE + GPIO_BSRR, mask);
}

static bool line_high(uint32_t mask)
{
    return (mmio_read32(K10_GPIOB_BASE + GPIO_IDR) & mask) == mask;
}

static bool clock_high(void)
{
    release_line(SCL_MASK);
    uint32_t started = hal_millis();
    for (uint32_t attempts = 0; attempts < 2000u; ++attempts) {
        if (line_high(SCL_MASK))
            return true;
        if ((uint32_t)(hal_millis() - started) >= 2u)
            break;
        hal_delay_us(1u);
    }
    return fail(SI4732_TIMEOUT);
}

static bool start_condition(void)
{
    release_line(SDA_MASK);
    if (!clock_high())
        return false;
    hal_delay_us(HALF_PERIOD_US);
    if (!line_high(SDA_MASK))
        return fail(SI4732_BUS_STUCK);
    drive_low(SDA_MASK);
    hal_delay_us(HALF_PERIOD_US);
    drive_low(SCL_MASK);
    return true;
}

static bool stop_condition(void)
{
    drive_low(SCL_MASK);
    drive_low(SDA_MASK);
    hal_delay_us(HALF_PERIOD_US);
    if (!clock_high()) {
        release_line(BUS_MASK);
        return false;
    }
    hal_delay_us(HALF_PERIOD_US);
    release_line(SDA_MASK);
    hal_delay_us(HALF_PERIOD_US);
    if (!line_high(SDA_MASK))
        return fail(SI4732_BUS_STUCK);
    return true;
}

static bool write_byte(uint8_t value)
{
    for (uint8_t bit = 0; bit < 8u; ++bit) {
        if ((value & 0x80u) != 0u)
            release_line(SDA_MASK);
        else
            drive_low(SDA_MASK);
        hal_delay_us(HALF_PERIOD_US);
        if (!clock_high())
            return false;
        hal_delay_us(HALF_PERIOD_US);
        drive_low(SCL_MASK);
        value = (uint8_t)(value << 1);
    }
    release_line(SDA_MASK);
    hal_delay_us(HALF_PERIOD_US);
    if (!clock_high())
        return false;
    hal_delay_us(HALF_PERIOD_US);
    bool acknowledged = !line_high(SDA_MASK);
    drive_low(SCL_MASK);
    return acknowledged ? true : fail(SI4732_NACK);
}

static bool read_byte(uint8_t *value, bool last)
{
    uint8_t result = 0u;
    release_line(SDA_MASK);
    for (uint8_t bit = 0; bit < 8u; ++bit) {
        hal_delay_us(HALF_PERIOD_US);
        if (!clock_high())
            return false;
        hal_delay_us(HALF_PERIOD_US);
        result = (uint8_t)((uint8_t)(result << 1) |
                           (line_high(SDA_MASK) ? 1u : 0u));
        drive_low(SCL_MASK);
    }
    if (last)
        release_line(SDA_MASK); /* NACK ends the response. */
    else
        drive_low(SDA_MASK);
    hal_delay_us(HALF_PERIOD_US);
    if (!clock_high())
        return false;
    hal_delay_us(HALF_PERIOD_US);
    drive_low(SCL_MASK);
    release_line(SDA_MASK);
    *value = result;
    return true;
}

static bool finish_packet(bool ok)
{
    enum si4732_error cause = last_error;
    bool stopped = stop_condition();
    if (!ok)
        last_error = cause; /* Preserve the command/address NACK or timeout. */
    return ok && stopped;
}

static bool write_packet(const uint8_t *data, size_t length)
{
    if (!bus_ready)
        return fail(SI4732_NOT_INITIALIZED);
    if (!start_condition()) {
        release_line(BUS_MASK);
        return false;
    }
    bool ok = write_byte((uint8_t)(K10_SI4732_ADDRESS << 1));
    for (size_t i = 0; i < length && ok; ++i)
        ok = write_byte(data[i]);
    return finish_packet(ok);
}

static bool read_packet(uint8_t *data, size_t length)
{
    if (!bus_ready)
        return fail(SI4732_NOT_INITIALIZED);
    if (!start_condition()) {
        release_line(BUS_MASK);
        return false;
    }
    bool ok = write_byte((uint8_t)((K10_SI4732_ADDRESS << 1) | 1u));
    for (size_t i = 0; i < length && ok; ++i)
        ok = read_byte(&data[i], i + 1u == length);
    return finish_packet(ok);
}

static bool wait_cts(void)
{
    uint32_t started = hal_millis();
    for (uint32_t attempts = 0; attempts < COMMAND_WAIT_MS; ++attempts) {
        uint8_t status;
        if (!read_packet(&status, 1u))
            return false;
        last_status = status;
        if ((status & CTS) != 0u) {
            if ((status & COMMAND_ERR) != 0u)
                return fail(SI4732_COMMAND_ERROR);
            return true;
        }
        if ((uint32_t)(hal_millis() - started) >= COMMAND_WAIT_MS)
            break;
        /* Independent attempt limit also bounds a stalled SysTick. */
        hal_delay_us(1000u);
    }
    return fail(SI4732_TIMEOUT);
}

static bool send_command(const uint8_t *command, size_t length)
{
    return wait_cts() && write_packet(command, length) && wait_cts();
}

static bool read_response(uint8_t *response, size_t length)
{
    if (!read_packet(response, length))
        return false;
    last_status = response[0];
    if ((last_status & (CTS | COMMAND_ERR)) != CTS)
        return fail(SI4732_COMMAND_ERROR);
    return true;
}

bool si4732_get_revision(struct si4732_revision *revision)
{
    if (revision == NULL)
        return fail(SI4732_INVALID_ARGUMENT);
    if (!powered_up)
        return fail(SI4732_NOT_INITIALIZED);
    const uint8_t command = 0x10u;
    uint8_t response[9];
    if (!send_command(&command, 1u) || !read_response(response, sizeof response))
        return false;
    revision->part_number = response[1];
    revision->firmware_major = response[2];
    revision->firmware_minor = response[3];
    revision->patch_id = (uint16_t)((uint16_t)response[4] << 8) | response[5];
    revision->component_major = response[6];
    revision->component_minor = response[7];
    revision->chip_revision = response[8];
    part_confirmed = revision->part_number == 0x20u;
    if (!part_confirmed)
        return fail(SI4732_UNEXPECTED_PART);
    last_error = SI4732_OK;
    return true;
}

bool si4732_init(void)
{
    if (powered_up) {
        struct si4732_revision revision;
        return si4732_get_revision(&revision);
    }
    bus_ready = false;
    part_confirmed = false;
    last_status = 0u;
    mmio_modify32(K10_RCC_BASE + RCC_IOPENR, 0u, UINT32_C(2));
    (void)mmio_read32(K10_RCC_BASE + RCC_IOPENR);
    /* Configure open drain before switching to output, with both latches high.
     * Internal pulls supplement board pull-ups; no unknown GPIO is changed. */
    mmio_modify32(K10_GPIOB_BASE + GPIO_OTYPER, 0u, BUS_MASK);
    release_line(BUS_MASK);
    mmio_modify32(K10_GPIOB_BASE + GPIO_PUPDR, BUS_MODE_MASK, BUS_OUTPUT_MODE);
    mmio_modify32(K10_GPIOB_BASE + GPIO_OSPEEDR, BUS_MODE_MASK, 0u);
    mmio_modify32(K10_GPIOB_BASE + GPIO_MODER, BUS_MODE_MASK, BUS_OUTPUT_MODE);
    hal_delay_us(HALF_PERIOD_US);
    if (!line_high(BUS_MASK))
        return fail(SI4732_BUS_STUCK);
    bus_ready = true;
    /* Observed three-byte POWER_UP: crystal oscillator, FM, analog outputs.
     * Hardware reset/power sequencing is unresolved and intentionally absent. */
    const uint8_t power_up[] = {0x01u, 0x10u, 0x05u};
    if (!write_packet(power_up, sizeof power_up) || !wait_cts())
        return false;
    /* AN332 requires >=500 ms before the first tune when XOSCEN is enabled. */
    for (uint16_t i = 0; i < 500u; ++i)
        hal_delay_us(1000u);
    powered_up = true;
    struct si4732_revision revision;
    return si4732_get_revision(&revision);
}

bool si4732_fm_tune(uint16_t frequency_10khz)
{
    if (!part_confirmed || !powered_up)
        return fail(SI4732_NOT_INITIALIZED);
    if (frequency_10khz < 6400u || frequency_10khz > 10800u)
        return fail(SI4732_INVALID_ARGUMENT);
    const uint8_t tune[] = {
        0x20u, 0x00u, (uint8_t)(frequency_10khz >> 8),
        (uint8_t)frequency_10khz, 0x00u
    };
    if (!send_command(tune, sizeof tune))
        return false;
    uint32_t started = hal_millis();
    bool complete = false;
    for (uint32_t attempts = 0; attempts < 1000u; ++attempts) {
        const uint8_t get_interrupts = 0x14u;
        if (!send_command(&get_interrupts, 1u))
            return false;
        if ((last_status & STC) != 0u) {
            complete = true;
            break;
        }
        if ((uint32_t)(hal_millis() - started) >= 2000u)
            break;
        hal_delay_us(1000u);
    }
    if (!complete)
        return fail(SI4732_TIMEOUT);
    const uint8_t tune_status[] = {0x22u, 0x01u}; /* acknowledge STC */
    uint8_t response[8];
    if (!send_command(tune_status, sizeof tune_status) ||
        !read_response(response, sizeof response))
        return false;
    uint16_t actual = (uint16_t)((uint16_t)response[2] << 8) | response[3];
    if (actual != frequency_10khz)
        return fail(SI4732_TUNE_MISMATCH);
    last_error = SI4732_OK;
    return true;
}

#else

bool si4732_init(void)
{
    last_error = SI4732_DISABLED;
    return false;
}

bool si4732_get_revision(struct si4732_revision *revision)
{
    (void)revision;
    last_error = SI4732_DISABLED;
    return false;
}

bool si4732_fm_tune(uint16_t frequency_10khz)
{
    (void)frequency_10khz;
    last_error = SI4732_DISABLED;
    return false;
}

#endif

enum si4732_error si4732_last_error(void)
{
    return last_error;
}

uint8_t si4732_last_status(void)
{
    return last_status;
}
