/* SPDX-License-Identifier: MIT
 * Independent read-only implementation of the interface recorded in
 * docs/pinmap.md. No vendor firmware code is included. */
#include "k10/spi_nor.h"
#include "k10/board.h"
#include "k10/hal.h"
#include "k10/mmio.h"

static enum spi_nor_error last_error = SPI_NOR_NOT_INITIALIZED;
static uint32_t capacity;

#if K10_ENABLE_SPI_NOR

#define GPIO_MODER       0x00u
#define GPIO_OTYPER      0x04u
#define GPIO_OSPEEDR     0x08u
#define GPIO_PUPDR       0x0cu
#define GPIO_BSRR        0x18u
#define GPIO_AFRL        0x20u
#define GPIO_BRR         0x28u
#define RCC_APBRSTR1     0x2cu
#define RCC_IOPENR       0x34u
#define RCC_APBENR1      0x3cu
#define RCC_SPI2_BIT     (UINT32_C(1) << 14)
#define SPI_CR1          0x00u
#define SPI_CR2          0x04u
#define SPI_SR           0x08u
#define SPI_DR           0x0cu
#define SPI_RXNE         UINT32_C(0x01)
#define SPI_TXE          UINT32_C(0x02)
#define SPI_FAULTS       UINT32_C(0x70) /* CRCERR, MODF, OVR */
#define SPI_BSY          UINT32_C(0x80)
#define TRANSFER_TIMEOUT_MS 10u
#define POLL_LIMIT       100000u
#define ADDRESS_LIMIT   UINT32_C(0x01000000)

static bool bus_ready;

static bool fail(enum spi_nor_error error)
{
    last_error = error;
    return false;
}

static void select_chip(bool selected)
{
    mmio_write32(K10_GPIOA_BASE + (selected ? GPIO_BRR : GPIO_BSRR),
                 UINT32_C(1) << K10_NOR_CS_PIN);
}

static bool wait_status(uint32_t mask, bool set)
{
    uint32_t started = hal_millis();
    for (uint32_t attempts = 0; attempts < POLL_LIMIT; ++attempts) {
        uint32_t status = mmio_read32(K10_SPI2_BASE + SPI_SR);
        if ((status & SPI_FAULTS) != 0u)
            return fail(SPI_NOR_BUS_ERROR);
        if (((status & mask) != 0u) == set)
            return true;
        if ((uint32_t)(hal_millis() - started) >= TRANSFER_TIMEOUT_MS)
            break;
    }
    return fail(SPI_NOR_TIMEOUT);
}

static bool exchange(uint8_t output, uint8_t *input)
{
    if (!wait_status(SPI_TXE, true))
        return false;
    mmio_write8(K10_SPI2_BASE + SPI_DR, output);
    if (!wait_status(SPI_RXNE, true))
        return false;
    uint8_t value = mmio_read8(K10_SPI2_BASE + SPI_DR);
    if (input != NULL)
        *input = value;
    return true;
}

static bool finish_transaction(bool success)
{
    /* The last RXNE alone does not guarantee that SCK is idle. */
    if (success)
        success = wait_status(SPI_BSY, false);
    select_chip(false);
    hal_delay_us(1u);
    if (!success) {
        /* Reinitialization is required after a peripheral fault. */
        bus_ready = false;
        capacity = 0u;
    }
    return success;
}

static uint32_t recognized_capacity(const uint8_t id[3])
{
    /* Exact documented IDs, not an assumption about the radio's fitted chip.
     * Puya P25Q32H, GigaDevice GD25Q32C, Winbond W25Q32JV variants.
     * Unknown types/densities require a reviewed addition or SFDP support. */
    bool known = (id[0] == 0x85u && id[1] == 0x60u) ||
                 (id[0] == 0xc8u && id[1] == 0x40u) ||
                 (id[0] == 0xefu && (id[1] == 0x40u || id[1] == 0x70u));
    if (!known || id[2] != 0x16u)
        return 0u;
    /* The exact IDs above define density as the byte-address exponent.
     * The allowlist and explicit limit prevent unsafe/overflowing shifts. */
    if (id[2] >= 32u)
        return 0u;
    uint32_t bytes = UINT32_C(1) << id[2];
    return bytes <= ADDRESS_LIMIT ? bytes : 0u;
}

static bool ready_for_read(void)
{
    /* A previous program/erase from another application could still be busy.
     * Observe SR1 without clearing or changing any persistent status bits. */
    uint32_t started = hal_millis();
    for (uint32_t attempts = 0; attempts < 100u; ++attempts) {
        uint8_t status = 0xffu;
        select_chip(true);
        bool ok = exchange(0x05u, NULL) && exchange(0xffu, &status);
        if (!finish_transaction(ok))
            return false;
        if ((status & 1u) == 0u)
            return true;
        if ((uint32_t)(hal_millis() - started) >= 100u)
            break;
        hal_delay_us(1000u);
    }
    return fail(SPI_NOR_TIMEOUT);
}

bool spi_nor_init(void)
{
    bus_ready = false;
    capacity = 0u;
    mmio_modify32(K10_RCC_BASE + RCC_IOPENR, 0u, UINT32_C(1));
    (void)mmio_read32(K10_RCC_BASE + RCC_IOPENR);

    /* Set the CS latch before making its pin an output. */
    select_chip(false);
    mmio_modify32(K10_GPIOA_BASE + GPIO_OTYPER, UINT32_C(0x0f), 0u);
    mmio_modify32(K10_GPIOA_BASE + GPIO_PUPDR, UINT32_C(0xff),
                  UINT32_C(0x55));
    mmio_modify32(K10_GPIOA_BASE + GPIO_OSPEEDR, UINT32_C(0xff),
                  UINT32_C(0x2a));
    mmio_modify32(K10_GPIOA_BASE + GPIO_AFRL, UINT32_C(0x0fff),
                  (K10_NOR_SCK_AF << (4u * K10_NOR_SCK_PIN)) |
                  (K10_NOR_MISO_AF << (4u * K10_NOR_MISO_PIN)) |
                  (K10_NOR_MOSI_AF << (4u * K10_NOR_MOSI_PIN)));
    /* PA0/1/2: alternate function; PA3: GPIO output. */
    mmio_modify32(K10_GPIOA_BASE + GPIO_MODER, UINT32_C(0xff),
                  UINT32_C(0x6a));

    mmio_modify32(K10_RCC_BASE + RCC_APBENR1, 0u, RCC_SPI2_BIT);
    (void)mmio_read32(K10_RCC_BASE + RCC_APBENR1);
    /* Reset this peripheral only; leave other GPIOs and peripherals alone. */
    mmio_modify32(K10_RCC_BASE + RCC_APBRSTR1, 0u, RCC_SPI2_BIT);
    mmio_modify32(K10_RCC_BASE + RCC_APBRSTR1, RCC_SPI2_BIT, 0u);
    mmio_write32(K10_SPI2_BASE + SPI_CR2, 0u); /* no DMA or SPI interrupts */
    /* CPOL=1, CPHA=1, master, 8-bit, MSB, software NSS high, PCLK / 256.
     * The sample uses /2; /256 is deliberate for initial hardware bring-up. */
    mmio_write32(K10_SPI2_BASE + SPI_CR1, UINT32_C(0x033f));
    mmio_write32(K10_SPI2_BASE + SPI_CR1, UINT32_C(0x037f));
    hal_delay_us(1u);
    if (!wait_status(SPI_TXE, true))
        return false;
    bus_ready = true;
    last_error = SPI_NOR_OK;
    return true;
}

bool spi_nor_read_jedec(uint8_t id[3])
{
    if (id == NULL)
        return fail(SPI_NOR_INVALID_ARGUMENT);
    if (!bus_ready)
        return fail(SPI_NOR_NOT_INITIALIZED);
    capacity = 0u;
    select_chip(true);
    bool ok = exchange(0x9fu, NULL);
    for (size_t i = 0; i < 3u && ok; ++i)
        ok = exchange(0xffu, &id[i]);
    if (!finish_transaction(ok))
        return false;
    if (id[0] == 0x00u || id[0] == 0xffu)
        return fail(SPI_NOR_NO_DEVICE);
    capacity = recognized_capacity(id);
    last_error = SPI_NOR_OK;
    return true;
}

bool spi_nor_read(uint32_t address, void *buf, size_t len)
{
    if (!bus_ready)
        return fail(SPI_NOR_NOT_INITIALIZED);
    if (buf == NULL && len != 0u)
        return fail(SPI_NOR_INVALID_ARGUMENT);
    if (capacity == 0u) {
        uint8_t id[3];
        if (!spi_nor_read_jedec(id))
            return false;
        if (capacity == 0u)
            return fail(SPI_NOR_UNSUPPORTED_ID);
    }
    if (address > capacity || len > (size_t)(capacity - address))
        return fail(SPI_NOR_OUT_OF_RANGE);
    if (len == 0u) {
        last_error = SPI_NOR_OK;
        return true;
    }
    if (!ready_for_read())
        return false;
    uint8_t *destination = (uint8_t *)buf;
    select_chip(true);
    bool ok = exchange(0x03u, NULL) &&
              exchange((uint8_t)(address >> 16), NULL) &&
              exchange((uint8_t)(address >> 8), NULL) &&
              exchange((uint8_t)address, NULL);
    for (size_t i = 0; i < len && ok; ++i)
        ok = exchange(0xffu, &destination[i]);
    if (!finish_transaction(ok))
        return false;
    last_error = SPI_NOR_OK;
    return true;
}

#else

bool spi_nor_init(void)
{
    capacity = 0u;
    last_error = SPI_NOR_DISABLED;
    return false;
}

bool spi_nor_read_jedec(uint8_t id[3])
{
    (void)id;
    last_error = SPI_NOR_DISABLED;
    return false;
}

bool spi_nor_read(uint32_t address, void *buf, size_t len)
{
    (void)address;
    (void)buf;
    (void)len;
    last_error = SPI_NOR_DISABLED;
    return false;
}

#endif

uint32_t spi_nor_capacity_bytes(void)
{
    return capacity;
}

enum spi_nor_error spi_nor_last_error(void)
{
    return last_error;
}
