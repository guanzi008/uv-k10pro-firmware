/* SPDX-License-Identifier: MIT
 * Host electrical/protocol models: SPI transactions and I2C clock edges.
 * These tests run the production C driver files, without executing MMIO. */
#include "k10/board.h"
#include "k10/si4732.h"
#include "k10/spi_nor.h"
#include "k10/mmio.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static uint32_t gpioa[16], gpiob[16], rcc[32], spi[16];
static uint32_t elapsed_us, accesses;
static bool frozen_tick;

static struct {
    bool selected, rx_ready, stall, fault, busy;
    uint8_t id[3], opcode, reply;
    uint32_t byte_index, address, last_read_address, transactions;
} nor;

enum i2c_phase {
    IDLE, RECEIVE, RX_ACK_PENDING, RX_ACK, TRANSMIT, TX_LAST,
    TX_ACK, TX_ACK_DONE, WAIT_STOP
};

static struct {
    bool present, sda_stuck, scl_stuck, stretch, slave_low;
    bool read_direction, address_next, tx_ack, cts_stall, command_error;
    bool stc_stall, tune_mismatch;
    enum i2c_phase phase;
    uint8_t bits, value, request[8], request_length, last_command;
    uint8_t response_index, part;
    uint32_t starts, writes, tunes, interrupt_polls;
    uint16_t frequency;
} receiver;

uint32_t hal_millis(void)
{
    return frozen_tick ? 0u : elapsed_us / 1000u;
}

void hal_delay_us(uint32_t microseconds)
{
    elapsed_us += microseconds;
}

static uint8_t flash_byte(uint32_t address)
{
    return (uint8_t)(address ^ (address >> 8) ^ 0xa5u);
}

static uint8_t response_byte(uint8_t index)
{
    uint8_t status = receiver.cts_stall ? 0u : 0x80u;
    if (receiver.command_error)
        status |= 0x40u;
    if (receiver.tunes != 0u && receiver.interrupt_polls >= 2u &&
        !receiver.stc_stall)
        status |= 1u;
    if (index == 0u)
        return status;
    if (receiver.last_command == 0x10u) {
        const uint8_t revision[] = {
            0u, 0x20u, '1', '0', 0x12u, 0x34u, '6', '0', 'A'
        };
        assert(index < sizeof revision);
        return index == 1u ? receiver.part : revision[index];
    }
    if (receiver.last_command == 0x22u) {
        uint16_t frequency = (uint16_t)(receiver.frequency +
                             (receiver.tune_mismatch ? 1u : 0u));
        if (index == 1u)
            return 1u;
        if (index == 2u)
            return (uint8_t)(frequency >> 8);
        if (index == 3u)
            return (uint8_t)frequency;
        return 0u;
    }
    return 0u;
}

static void accept_request(void)
{
    if (receiver.request_length == 0u)
        return;
    ++receiver.writes;
    receiver.last_command = receiver.request[0];
    switch (receiver.last_command) {
    case 0x01u:
        assert(receiver.request_length == 3u);
        assert(receiver.request[1] == 0x10u && receiver.request[2] == 0x05u);
        break;
    case 0x10u:
        assert(receiver.request_length == 1u);
        break;
    case 0x20u:
        assert(receiver.request_length == 5u);
        assert(receiver.request[1] == 0u && receiver.request[4] == 0u);
        receiver.frequency = (uint16_t)((uint16_t)receiver.request[2] << 8) |
                             receiver.request[3];
        ++receiver.tunes;
        receiver.interrupt_polls = 0u;
        break;
    case 0x14u:
        assert(receiver.request_length == 1u);
        ++receiver.interrupt_polls;
        break;
    case 0x22u:
        assert(receiver.request_length == 2u && receiver.request[1] == 1u);
        break;
    default:
        assert(!"Unexpected receiver command");
    }
}

static void i2c_start(void)
{
    ++receiver.starts;
    receiver.phase = RECEIVE;
    receiver.bits = 0u;
    receiver.value = 0u;
    receiver.address_next = true;
    receiver.read_direction = false;
    receiver.request_length = 0u;
    receiver.response_index = 0u;
    receiver.slave_low = false;
}

static void i2c_rise(bool sda)
{
    switch (receiver.phase) {
    case RECEIVE:
        receiver.value = (uint8_t)((uint8_t)(receiver.value << 1) | (sda ? 1u : 0u));
        if (++receiver.bits == 8u) {
            if (receiver.address_next) {
                assert(receiver.value == 0x22u || receiver.value == 0x23u);
                receiver.read_direction = (receiver.value & 1u) != 0u;
                receiver.address_next = false;
            } else {
                assert(receiver.request_length < sizeof receiver.request);
                receiver.request[receiver.request_length++] = receiver.value;
            }
            receiver.phase = RX_ACK_PENDING;
        }
        break;
    case TRANSMIT:
        if (++receiver.bits == 8u)
            receiver.phase = TX_LAST;
        break;
    case TX_ACK:
        receiver.tx_ack = !sda;
        receiver.phase = TX_ACK_DONE;
        break;
    default:
        break;
    }
}

static void i2c_fall(void)
{
    switch (receiver.phase) {
    case RX_ACK_PENDING:
        receiver.phase = RX_ACK;
        receiver.slave_low = receiver.present;
        break;
    case RX_ACK:
        receiver.bits = 0u;
        receiver.value = 0u;
        receiver.phase = receiver.read_direction ? TRANSMIT : RECEIVE;
        receiver.slave_low = receiver.read_direction &&
                             (response_byte(receiver.response_index) & 0x80u) == 0u;
        break;
    case TRANSMIT:
        receiver.slave_low = (response_byte(receiver.response_index) &
                              (0x80u >> receiver.bits)) == 0u;
        break;
    case TX_LAST:
        receiver.phase = TX_ACK;
        receiver.slave_low = false;
        break;
    case TX_ACK_DONE:
        receiver.slave_low = false;
        if (receiver.tx_ack) {
            ++receiver.response_index;
            receiver.bits = 0u;
            receiver.phase = TRANSMIT;
            receiver.slave_low = (response_byte(receiver.response_index) & 0x80u) == 0u;
        } else {
            receiver.phase = WAIT_STOP;
        }
        break;
    default:
        break;
    }
}

static void i2c_gpio_change(uint32_t previous, uint32_t current)
{
    bool old_clock = (previous & 0x100u) != 0u;
    bool new_clock = (current & 0x100u) != 0u;
    bool old_data = (previous & 0x200u) != 0u;
    bool new_data = (current & 0x200u) != 0u;
    if (old_clock && new_clock && old_data != new_data) {
        if (!new_data) {
            i2c_start();
        } else {
            accept_request();
            receiver.phase = IDLE;
            receiver.slave_low = false;
        }
    } else if (!old_clock && new_clock) {
        i2c_rise(new_data);
    } else if (old_clock && !new_clock) {
        i2c_fall();
    }
}

static uint32_t *register_word(uintptr_t address)
{
    assert((address & 3u) == 0u);
    if (address >= K10_GPIOA_BASE && address < K10_GPIOA_BASE + sizeof gpioa)
        return &gpioa[(address - K10_GPIOA_BASE) / 4u];
    if (address >= K10_GPIOB_BASE && address < K10_GPIOB_BASE + sizeof gpiob)
        return &gpiob[(address - K10_GPIOB_BASE) / 4u];
    if (address >= K10_RCC_BASE && address < K10_RCC_BASE + sizeof rcc)
        return &rcc[(address - K10_RCC_BASE) / 4u];
    if (address >= K10_SPI2_BASE && address < K10_SPI2_BASE + sizeof spi)
        return &spi[(address - K10_SPI2_BASE) / 4u];
    assert(!"Access to an undocumented peripheral");
    return NULL;
}

uint32_t mmio_read32(uintptr_t address)
{
    ++accesses;
    assert(address != K10_SPI2_BASE + 0x0cu); /* DR must be byte-accessed. */
    if (address == K10_SPI2_BASE + 8u)
        return nor.stall ? 0u : nor.fault ? 0x20u : 2u | (nor.rx_ready ? 1u : 0u);
    if (address == K10_GPIOB_BASE + 0x10u) {
        uint32_t value = gpiob[0x14u / 4u];
        if (receiver.sda_stuck || receiver.slave_low)
            value &= ~UINT32_C(0x200);
        if (receiver.scl_stuck || (receiver.stretch && receiver.starts != 0u))
            value &= ~UINT32_C(0x100);
        return value;
    }
    return *register_word(address);
}

void mmio_write32(uintptr_t address, uint32_t value)
{
    ++accesses;
    assert(address != K10_SPI2_BASE + 0x0cu);
    if (address == K10_GPIOA_BASE + 0x18u || address == K10_GPIOA_BASE + 0x28u) {
        assert(value == 8u); /* Only the NOR select is a GPIO output here. */
        bool selected = address == K10_GPIOA_BASE + 0x28u;
        if (selected) {
            assert(!nor.selected);
            nor.byte_index = 0u;
            nor.address = 0u;
        } else if (nor.selected) {
            ++nor.transactions;
        }
        nor.selected = selected;
    }
    if (address == K10_GPIOB_BASE + 0x18u || address == K10_GPIOB_BASE + 0x28u) {
        assert((value & ~UINT32_C(0x300)) == 0u);
        uint32_t previous = gpiob[0x14u / 4u];
        uint32_t current = address == K10_GPIOB_BASE + 0x18u ?
                           previous | value : previous & ~value;
        gpiob[0x14u / 4u] = current;
        i2c_gpio_change(previous, current);
    }
    *register_word(address) = value;
}

void mmio_write8(uintptr_t address, uint8_t value)
{
    ++accesses;
    assert(address == K10_SPI2_BASE + 0x0cu && nor.selected && !nor.rx_ready);
    nor.reply = 0xffu;
    if (nor.byte_index == 0u) {
        /* An accidental write/erase command fails the test immediately. */
        assert(value == 0x03u || value == 0x05u || value == 0x9fu);
        nor.opcode = value;
    } else if (nor.opcode == 0x9fu) {
        assert(nor.byte_index <= 3u);
        nor.reply = nor.id[nor.byte_index - 1u];
    } else if (nor.opcode == 0x05u) {
        nor.reply = nor.busy ? 1u : 0u;
    } else if (nor.byte_index <= 3u) {
        nor.address = (nor.address << 8) | value;
        if (nor.byte_index == 3u)
            nor.last_read_address = nor.address;
    } else {
        assert(nor.address < (UINT32_C(1) << 22));
        nor.reply = flash_byte(nor.address++);
    }
    ++nor.byte_index;
    nor.rx_ready = true;
}

uint8_t mmio_read8(uintptr_t address)
{
    ++accesses;
    assert(address == K10_SPI2_BASE + 0x0cu && nor.rx_ready);
    nor.rx_ready = false;
    return nor.reply;
}

static void defaults(void)
{
    gpioa[0] = 0xa5a5ff00u;
    gpiob[0] = 0x5a50000fu;
    nor.id[0] = 0x85u;
    nor.id[1] = 0x60u;
    nor.id[2] = 0x16u;
    receiver.present = true;
    receiver.part = 0x20u;
}

static void test_nor_normal(void)
{
    uint8_t id[3], data[64];
    assert(spi_nor_init());
    assert((gpioa[0] & ~0xffu) == 0xa5a5ff00u);
    assert((gpioa[0x20u / 4u] & 0xfffu) == 0x998u);
    assert(spi[0] == 0x37fu);
    assert(spi_nor_read_jedec(id));
    assert(memcmp(id, nor.id, 3u) == 0);
    assert(spi_nor_capacity_bytes() == 0x400000u);
    assert(spi_nor_read(0x123456u, data, sizeof data));
    assert(nor.last_read_address == 0x123456u);
    for (size_t i = 0; i < sizeof data; ++i)
        assert(data[i] == flash_byte(0x123456u + (uint32_t)i));
    assert(spi_nor_read(0x3fffffu, data, 1u));
    uint32_t transactions = nor.transactions;
    assert(spi_nor_read(0x400000u, NULL, 0u));
    assert(!spi_nor_read(0x400000u, data, 1u));
    assert(spi_nor_last_error() == SPI_NOR_OUT_OF_RANGE);
    assert(!spi_nor_read(UINT32_MAX, data, 1u));
    assert(!spi_nor_read(1u, data, SIZE_MAX));
    assert(!spi_nor_read(0u, NULL, 1u));
    assert(spi_nor_last_error() == SPI_NOR_INVALID_ARGUMENT);
    assert(nor.transactions == transactions && !nor.selected);
}

static void test_nor_unknown(void)
{
    uint8_t id[3], byte;
    nor.id[0] = 0xaau;
    nor.id[2] = 0xffu; /* Must not become an unchecked shift count. */
    assert(spi_nor_init() && spi_nor_read_jedec(id));
    assert(memcmp(id, nor.id, 3u) == 0);
    assert(spi_nor_capacity_bytes() == 0u);
    assert(!spi_nor_read(0u, &byte, 1u));
    assert(spi_nor_last_error() == SPI_NOR_UNSUPPORTED_ID);
    assert(nor.opcode == 0x9fu && !nor.selected);
}

static void test_si_normal(void)
{
    struct si4732_revision revision;
    assert(si4732_init());
    assert(elapsed_us >= 500000u);
    assert((gpiob[0] & ~0xf0000u) == (0x5a50000fu & ~0xf0000u));
    assert((gpiob[1] & 0x300u) == 0x300u); /* Both lines open drain. */
    assert(si4732_get_revision(&revision));
    assert(revision.part_number == 0x20u && revision.patch_id == 0x1234u);
    assert(revision.firmware_major == '1' && revision.chip_revision == 'A');
    uint32_t writes = receiver.writes;
    assert(!si4732_fm_tune(6399u) && !si4732_fm_tune(10801u));
    assert(receiver.writes == writes);
    assert(si4732_fm_tune(10110u));
    assert(receiver.frequency == 10110u && receiver.interrupt_polls >= 2u);
    assert(receiver.last_command == 0x22u);
    assert(si4732_last_error() == SI4732_OK);
}

int main(int argc, char **argv)
{
    assert(argc == 2);
    defaults();
    if (strcmp(argv[1], "nor-normal") == 0) {
        test_nor_normal();
    } else if (strcmp(argv[1], "nor-unknown") == 0) {
        test_nor_unknown();
    } else if (strcmp(argv[1], "nor-absent") == 0) {
        uint8_t id[3];
        memset(nor.id, 0xff, sizeof nor.id);
        assert(spi_nor_init() && !spi_nor_read_jedec(id));
        assert(spi_nor_last_error() == SPI_NOR_NO_DEVICE);
    } else if (strcmp(argv[1], "nor-timeout") == 0) {
        nor.stall = frozen_tick = true;
        assert(!spi_nor_init() && spi_nor_last_error() == SPI_NOR_TIMEOUT);
    } else if (strcmp(argv[1], "nor-fault") == 0) {
        nor.fault = true;
        assert(!spi_nor_init() && spi_nor_last_error() == SPI_NOR_BUS_ERROR);
    } else if (strcmp(argv[1], "nor-transfer-timeout") == 0) {
        uint8_t id[3];
        assert(spi_nor_init());
        nor.stall = frozen_tick = true;
        assert(!spi_nor_read_jedec(id) && spi_nor_last_error() == SPI_NOR_TIMEOUT);
        assert(!nor.selected && spi_nor_capacity_bytes() == 0u);
        assert(!spi_nor_read_jedec(id));
        assert(spi_nor_last_error() == SPI_NOR_NOT_INITIALIZED);
    } else if (strcmp(argv[1], "nor-busy") == 0) {
        uint8_t data;
        nor.busy = true;
        assert(spi_nor_init() && !spi_nor_read(0u, &data, 1u));
        assert(spi_nor_last_error() == SPI_NOR_TIMEOUT);
        assert(nor.opcode == 0x05u && !nor.selected);
    } else if (strcmp(argv[1], "si-normal") == 0) {
        test_si_normal();
    } else if (strcmp(argv[1], "si-absent") == 0) {
        receiver.present = false;
        assert(!si4732_init() && si4732_last_error() == SI4732_NACK);
        assert((gpiob[0x14u / 4u] & 0x300u) == 0x300u);
    } else if (strcmp(argv[1], "si-stuck") == 0) {
        receiver.sda_stuck = true;
        assert(!si4732_init() && si4732_last_error() == SI4732_BUS_STUCK);
        assert(receiver.writes == 0u);
    } else if (strcmp(argv[1], "si-stretch-timeout") == 0) {
        receiver.stretch = frozen_tick = true;
        assert(!si4732_init() && si4732_last_error() == SI4732_TIMEOUT);
        assert((gpiob[0x14u / 4u] & 0x300u) == 0x300u);
    } else if (strcmp(argv[1], "si-cts-timeout") == 0) {
        receiver.cts_stall = frozen_tick = true;
        assert(!si4732_init() && si4732_last_error() == SI4732_TIMEOUT);
        assert(receiver.writes == 1u);
    } else if (strcmp(argv[1], "si-command-error") == 0) {
        receiver.command_error = true;
        assert(!si4732_init() && si4732_last_error() == SI4732_COMMAND_ERROR);
    } else if (strcmp(argv[1], "si-wrong-part") == 0) {
        struct si4732_revision revision;
        receiver.part = 0x1fu;
        assert(!si4732_init() && si4732_last_error() == SI4732_UNEXPECTED_PART);
        assert(!si4732_get_revision(&revision) && revision.part_number == 0x1fu);
        assert(!si4732_fm_tune(10000u) && receiver.tunes == 0u);
    } else if (strcmp(argv[1], "si-tune-timeout") == 0) {
        assert(si4732_init());
        receiver.stc_stall = frozen_tick = true;
        assert(!si4732_fm_tune(10000u) && si4732_last_error() == SI4732_TIMEOUT);
    } else if (strcmp(argv[1], "si-tune-mismatch") == 0) {
        assert(si4732_init());
        receiver.tune_mismatch = true;
        assert(!si4732_fm_tune(10000u));
        assert(si4732_last_error() == SI4732_TUNE_MISMATCH);
    } else if (strcmp(argv[1], "disabled") == 0) {
        uint8_t id[3];
        struct si4732_revision revision;
        assert(!spi_nor_init() && !spi_nor_read_jedec(id));
        assert(!spi_nor_read(0u, id, sizeof id));
        assert(spi_nor_last_error() == SPI_NOR_DISABLED);
        assert(!si4732_init() && !si4732_get_revision(&revision));
        assert(!si4732_fm_tune(10000u) && si4732_last_error() == SI4732_DISABLED);
        assert(accesses == 0u);
    } else {
        assert(!"Unknown test scenario");
    }
    printf("PASS %s\n", argv[1]);
    return 0;
}
