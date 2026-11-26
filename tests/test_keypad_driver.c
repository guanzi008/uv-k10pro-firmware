/* SPDX-License-Identifier: MIT */
#include "k10/board.h"
#include "k10/keypad.h"
#include "k10/mmio.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

static const uintptr_t ports[] = {K10_GPIOA_BASE, K10_GPIOB_BASE, K10_GPIOC_BASE, K10_GPIOF_BASE};
static uint32_t registers[4][16];
static uint32_t rcc;
static unsigned accesses;
static int contact_row = -1, contact_column = -1;
static uint32_t direct_low;
static bool ptt_low;
static const unsigned row_port[] = {3u, 1u, 1u, 0u};
static const unsigned row_pin[] = {8u, 4u, 3u, 14u};

static unsigned port_index(uintptr_t address, unsigned *offset)
{
    for (unsigned i = 0u; i < 4u; ++i) {
        if (address >= ports[i] && address < ports[i] + 0x40u) {
            assert((address & 3u) == 0u);
            *offset = (unsigned)(address - ports[i]);
            return i;
        }
    }
    assert(!"unexpected MMIO address");
    return 0u;
}

uint32_t mmio_read32(uintptr_t address)
{
    ++accesses;
    if (address == K10_RCC_BASE + 0x34u) { return rcc; }
    unsigned offset;
    unsigned port = port_index(address, &offset);
    if (offset != 0x10u) { return registers[port][offset / 4u]; }
    uint32_t value = UINT32_C(0xffff);
    if (port == 1u) { value &= ~direct_low; }
    if (port == 2u && ptt_low) { value &= ~(UINT32_C(1) << 13); }
    if (contact_row >= 0 && contact_column >= 0 &&
        (registers[row_port[contact_row]][5] & (UINT32_C(1) << row_pin[contact_row])) == 0u) {
        if (contact_column < 4 && port == 1u) {
            value &= ~(UINT32_C(1) << (15 - contact_column));
        } else if (contact_column == 4 && port == 0u) {
            value &= ~(UINT32_C(1) << 8);
        }
    }
    return value;
}

void mmio_write32(uintptr_t address, uint32_t value)
{
    ++accesses;
    if (address == K10_RCC_BASE + 0x34u) { rcc = value; return; }
    unsigned offset;
    unsigned port = port_index(address, &offset);
    assert(offset == 0u || offset == 4u || offset == 8u || offset == 12u ||
           offset == 0x18u || offset == 0x28u);
    if (offset == 0x18u) {
        registers[port][5] = (registers[port][5] & ~(value >> 16)) | (value & 0xffffu);
    } else if (offset == 0x28u) {
        registers[port][5] &= ~(value & 0xffffu);
    } else { registers[port][offset / 4u] = value; }
}

uint32_t hal_millis(void) { return 0u; }
void hal_delay_us(uint32_t us) { assert(us == 1u); }

#if K10_ENABLE_KEYPAD
static void rows_high(void)
{
    for (unsigned i = 0u; i < 4u; ++i) {
        assert((registers[row_port[i]][5] & (UINT32_C(1) << row_pin[i])) != 0u);
    }
}

static struct keypad_state poll(uint32_t time)
{
    struct keypad_state state;
    assert(keypad_poll(time, &state));
    assert(keypad_last_error() == KEYPAD_OK);
    rows_high();
    return state;
}

static void geometry(void)
{
    static const uint8_t expected[4][5] = {
        {17u,1u,4u,7u,10u}, {12u,2u,5u,8u,11u},
        {13u,3u,6u,9u,KEYPAD_NONE}, {18u,14u,0u,15u,16u}
    };
    assert(keypad_init());
    assert(poll(0u).raw_key == KEYPAD_NONE);
    for (int row = 0; row < 4; ++row) {
        for (int col = 0; col < 5; ++col) {
            contact_row = row;
            contact_column = col;
            assert(poll(1u).raw_key == expected[row][col]);
        }
    }
    contact_row = contact_column = -1;
    direct_low = 1u << 13;
    assert(poll(2u).raw_key == 25u);
    direct_low |= 1u << 14;
    assert(poll(3u).raw_key == 23u);
    direct_low |= 1u << 15;
    ptt_low = true;
    struct keypad_state state = poll(4u);
    assert(state.raw_key == 24u && state.raw_ptt);
    /* Independent PTT must remain visible during a matrix or side key. */
    direct_low = 0u;
    contact_row = 3; contact_column = 2;
    state = poll(5u);
    assert(state.raw_key == 0u && state.raw_ptt);
}

static void debounce(void)
{
    assert(keypad_init());
    contact_row = 0; contact_column = 1; /* key 1 */
    struct keypad_state state = poll(100u);
    assert(state.key == KEYPAD_NONE && state.events == 0u);
    assert(poll(119u).key == KEYPAD_NONE);
    contact_row = -1;
    assert(poll(120u).events == 0u); /* a bounce restarts the interval */
    contact_row = 0;
    assert(poll(121u).events == 0u);
    state = poll(141u);
    assert(state.key == 1u && state.events == KEYPAD_PRESS);
    assert(state.previous_key == KEYPAD_NONE);
    assert(poll(200u).events == 0u); /* no fabricated repeats */
    contact_row = 1; contact_column = 2; /* change directly to key 5 */
    assert(poll(201u).events == 0u);
    state = poll(221u);
    assert(state.key == 5u && state.previous_key == 1u);
    assert(state.events == (KEYPAD_RELEASE | KEYPAD_PRESS));
    contact_row = -1;
    ptt_low = true;
    assert(poll(222u).events == 0u);
    state = poll(242u);
    assert(state.key == KEYPAD_NONE && state.ptt);
    assert(state.events == (KEYPAD_RELEASE | KEYPAD_PTT_PRESS));
    assert(keypad_init()); /* reset candidate and stable state */
    contact_row = 3; contact_column = 2;
    ptt_low = true;
    assert(poll(UINT32_MAX - 9u).events == 0u);
    assert(poll(9u).events == 0u); /* elapsed 19 ms across wrap */
    state = poll(10u);
    assert(state.key == 0u && state.ptt);
    assert(state.events == (KEYPAD_PRESS | KEYPAD_PTT_PRESS));
    ptt_low = false;
    assert(poll(11u).events == 0u);
    state = poll(31u);
    assert(!state.ptt && state.events == KEYPAD_PTT_RELEASE);
}

static uint32_t two_bit_mask(uint32_t pins)
{
    uint32_t mask = 0u;
    for (unsigned i = 0u; i < 16u; ++i) {
        if (pins & (1u << i)) { mask |= UINT32_C(3) << (i * 2u); }
    }
    return mask;
}

static void preservation(void)
{
    uint32_t before[4][16];
    static const uint32_t row_masks[] = {1u << 14, (1u << 3) | (1u << 4), 0u, 1u << 8};
    static const uint32_t input_masks[] = {1u << 8, 0xf000u, 1u << 13, 0u};
    for (unsigned p = 0u; p < 4u; ++p) {
        for (unsigned r = 0u; r < 16u; ++r) { registers[p][r] = UINT32_C(0xaaaaaaaa); }
    }
    rcc = 0x100u;
    memcpy(before, registers, sizeof(before));
    assert(keypad_init());
    assert(rcc == 0x127u);
    for (unsigned p = 0u; p < 4u; ++p) {
        uint32_t mode_mask = two_bit_mask(row_masks[p] | input_masks[p]);
        uint32_t row_mode_mask = two_bit_mask(row_masks[p]);
        assert((registers[p][0] & ~mode_mask) == (before[p][0] & ~mode_mask));
        assert((registers[p][3] & ~mode_mask) == (before[p][3] & ~mode_mask));
        assert((registers[p][1] & ~row_masks[p]) == (before[p][1] & ~row_masks[p]));
        assert((registers[p][2] & ~row_mode_mask) == (before[p][2] & ~row_mode_mask));
        assert((registers[p][5] & ~row_masks[p]) == (before[p][5] & ~row_masks[p]));
        assert(registers[p][8] == before[p][8] && registers[p][9] == before[p][9]);
        for (unsigned bit = 0u; bit < 16u; ++bit) {
            if ((row_masks[p] | input_masks[p]) & (1u << bit)) {
                assert(((registers[p][3] >> (bit * 2u)) & 3u) == 1u);
                assert(((registers[p][0] >> (bit * 2u)) & 3u) ==
                       ((row_masks[p] & (1u << bit)) ? 1u : 0u));
            }
            if (row_masks[p] & (1u << bit)) {
                assert((registers[p][1] & (1u << bit)) == 0u);
                assert(((registers[p][2] >> (bit * 2u)) & 3u) == 3u);
            }
        }
    }
    rows_high();
    (void)poll(0u);
}

static void invalid(void)
{
    struct keypad_state state;
    memset(&state, 0x5a, sizeof(state));
    struct keypad_state original = state;
    assert(!keypad_poll(0u, &state));
    assert(keypad_last_error() == KEYPAD_NOT_INITIALIZED);
    assert(memcmp(&state, &original, sizeof(state)) == 0);
    assert(!keypad_poll(0u, NULL));
    assert(keypad_last_error() == KEYPAD_INVALID_ARGUMENT && accesses == 0u);
    assert(keypad_init());
    unsigned previous_accesses = accesses;
    assert(!keypad_poll(0u, NULL));
    assert(accesses == previous_accesses);
    assert(poll(0u).raw_key == KEYPAD_NONE);
}
#endif

int main(int argc, char **argv)
{
    assert(argc == 2);
    assert(strcmp(keypad_label(0u), "0") == 0);
    assert(strcmp(keypad_label(KEYPAD_NONE), "NONE") == 0);
    assert(strcmp(keypad_label(21u), "PTT") == 0);
    assert(strcmp(keypad_label(24u), "SK1") == 0);
    assert(strcmp(keypad_label(199u), "UNKNOWN") == 0);
#if K10_ENABLE_KEYPAD
    if (strcmp(argv[1], "geometry") == 0) { geometry(); }
    else if (strcmp(argv[1], "debounce") == 0) { debounce(); }
    else if (strcmp(argv[1], "preservation") == 0) { preservation(); }
    else if (strcmp(argv[1], "invalid") == 0) { invalid(); }
    else { assert(!"unknown test"); }
#else
    assert(strcmp(argv[1], "disabled") == 0);
    assert(!keypad_init());
    assert(keypad_last_error() == KEYPAD_DISABLED);
    struct keypad_state state = {0};
    assert(!keypad_poll(0u, &state));
    assert(!keypad_poll(0u, NULL));
    assert(keypad_last_error() == KEYPAD_DISABLED && accesses == 0u);
#endif
    printf("PASS: keypad %s\n", argv[1]);
    return 0;
}
