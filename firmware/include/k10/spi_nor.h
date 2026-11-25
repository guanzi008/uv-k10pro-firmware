/* SPDX-License-Identifier: MIT */
#ifndef K10_SPI_NOR_H
#define K10_SPI_NOR_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum spi_nor_error {
    SPI_NOR_OK = 0,
    SPI_NOR_DISABLED,
    SPI_NOR_NOT_INITIALIZED,
    SPI_NOR_INVALID_ARGUMENT,
    SPI_NOR_TIMEOUT,
    SPI_NOR_BUS_ERROR,
    SPI_NOR_NO_DEVICE,
    SPI_NOR_UNSUPPORTED_ID,
    SPI_NOR_OUT_OF_RANGE
};

/* Configure SPI2 and the four verified GPIOs. Does not identify the chip. */
bool spi_nor_init(void);

/* Read the raw 9Fh response. A readable, unknown ID is still returned; its
 * capacity remains zero and array reads are refused. Null id is an error. */
bool spi_nor_read_jedec(uint8_t id[3]);

/* Read an array interval only after a supported JEDEC ID establishes its size.
 * Bounds are checked using subtraction, including size_t overflow cases.
 * A zero-length read permits buf == NULL and address == capacity.
 * On transfer failure the destination may contain a partial read. */
bool spi_nor_read(uint32_t address, void *buf, size_t len);

/* Last recognized JEDEC capacity, or zero when unknown/absent. */
uint32_t spi_nor_capacity_bytes(void);
enum spi_nor_error spi_nor_last_error(void);

/* Single caller only: these blocking routines must not run from interrupts.
 * The interface deliberately offers no write, erase, unlock or status write. */

#endif
