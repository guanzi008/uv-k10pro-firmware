/* SPDX-License-Identifier: MIT */
#include "k10/board.h"
#include "k10/diagnostics.h"
#include "k10/frontpanel.h"
#include "k10/keypad.h"

/* Small, independently drawn 5x7 diagnostic alphabet, as five-bit rows.
 * No glyph data is copied from the vendor image. */
struct glyph { char character; uint8_t rows[7]; };
static const struct glyph alphabet[] = {
    {'A',{14,17,17,31,17,17,17}}, {'B',{30,17,17,30,17,17,30}},
    {'C',{14,17,16,16,16,17,14}}, {'D',{30,17,17,17,17,17,30}},
    {'E',{31,16,16,30,16,16,31}}, {'F',{31,16,16,30,16,16,16}},
    {'G',{14,17,16,23,17,17,15}}, {'H',{17,17,17,31,17,17,17}},
    {'I',{14,4,4,4,4,4,14}}, {'J',{7,2,2,2,18,18,12}},
    {'K',{17,18,20,24,20,18,17}}, {'L',{16,16,16,16,16,16,31}},
    {'M',{17,27,21,21,17,17,17}}, {'N',{17,25,25,21,19,19,17}},
    {'O',{14,17,17,17,17,17,14}}, {'P',{30,17,17,30,16,16,16}},
    {'Q',{14,17,17,17,21,18,13}}, {'R',{30,17,17,30,20,18,17}},
    {'S',{15,16,16,14,1,1,30}}, {'T',{31,4,4,4,4,4,4}},
    {'U',{17,17,17,17,17,17,14}}, {'V',{17,17,17,17,17,10,4}},
    {'W',{17,17,17,21,21,27,17}}, {'X',{17,17,10,4,10,17,17}},
    {'Y',{17,17,10,4,4,4,4}}, {'Z',{31,1,2,4,8,16,31}},
    {'0',{14,17,19,21,25,17,14}}, {'1',{4,12,4,4,4,4,14}},
    {'2',{14,17,1,2,4,8,31}}, {'3',{30,1,1,14,1,1,30}},
    {'4',{2,6,10,18,31,2,2}}, {'5',{31,16,16,30,1,1,30}},
    {'6',{14,16,16,30,17,17,14}}, {'7',{31,1,2,4,8,8,8}},
    {'8',{14,17,17,14,17,17,14}}, {'9',{14,17,17,15,1,1,14}},
    {'-',{0,0,0,31,0,0,0}}, {'/',{1,1,2,4,8,16,16}},
    {':',{0,4,4,0,4,4,0}}, {'.',{0,0,0,0,0,6,6}},
    {'?',{14,17,1,2,4,0,4}}, {' ',{0,0,0,0,0,0,0}}
};

struct line { char text[22]; size_t count; };

static void append(struct line *line, const char *text)
{
    while (*text != '\0' && line->count < sizeof(line->text) - 1u) {
        line->text[line->count++] = *text++;
    }
    line->text[line->count] = '\0';
}

static void number(struct line *line, uint32_t value, uint32_t radix, size_t width)
{
    static const char digits[] = "0123456789ABCDEF";
    char reverse[10];
    size_t count = 0u;
    do {
        reverse[count++] = digits[value % radix];
        value /= radix;
    } while (value != 0u && count < sizeof(reverse));
    while (count < width && count < sizeof(reverse)) { reverse[count++] = '0'; }
    while (count != 0u) {
        char digit[2] = {reverse[--count], '\0'};
        append(line, digit);
    }
}

static void draw_line(uint8_t *framebuffer, unsigned page, const struct line *line,
                      bool inverted)
{
    uint8_t *pixels = framebuffer + page * 128u;
    for (unsigned i = 0u; i < 128u; ++i) { pixels[i] = inverted ? 0xffu : 0u; }
    for (size_t cell = 0u; cell < line->count; ++cell) {
        const uint8_t *rows = alphabet[sizeof(alphabet) / sizeof(alphabet[0]) - 2u].rows;
        for (size_t i = 0u; i < sizeof(alphabet) / sizeof(alphabet[0]); ++i) {
            if (alphabet[i].character == line->text[cell]) { rows = alphabet[i].rows; break; }
        }
        for (unsigned col = 0u; col < 5u; ++col) {
            uint8_t bits = 0u;
            for (unsigned row = 0u; row < 7u; ++row) {
                if ((rows[row] & (1u << (4u - col))) != 0u) { bits |= (uint8_t)(1u << row); }
            }
            pixels[cell * 6u + col] ^= bits;
        }
    }
}

bool frontpanel_render(uint8_t *framebuffer, size_t length,
                       const struct frontpanel_view *view)
{
    if (framebuffer == NULL || view == NULL || length != 1024u) { return false; }
    for (unsigned page = 0u; page < 8u; ++page) {
        struct line line = {{0}, 0u};
        switch (page) {
        case 0u:
            append(&line, "UV-K10 PRO DIAG");
            break;
        case 1u:
            append(&line, "PY32F071 ");
            number(&line, view->clock_hz / 1000u, 10u, 1u);
            append(&line, "KHZ");
            if ((view->flags & K10_FLAG_CLOCK_KNOWN) == 0u) { append(&line, "?"); }
            break;
        case 2u:
            append(&line, "NOR ID ");
            if ((view->flags & K10_FLAG_JEDEC_READ) != 0u) {
                number(&line, view->jedec_id & UINT32_C(0xffffff), 16u, 6u);
            } else { append(&line, "------"); }
            break;
        case 3u:
            append(&line, "FLASH ");
            if (view->flash_error != 0u) {
                append(&line, "ERR "); number(&line, view->flash_error, 10u, 1u);
            } else if ((view->flags & K10_FLAG_JEDEC_READ) == 0u) {
                append(&line, "WAIT");
            } else if (view->flash_capacity == 0u) {
                append(&line, "SIZE UNKNOWN");
            } else if (view->flash_capacity != K10_NOR_EXPECTED_CAPACITY_BYTES) {
                append(&line, "SIZE MISMATCH");
            } else { append(&line, "4MIB"); }
            break;
        case 4u:
            append(&line, "SI4732 ");
            if (view->receiver_error != 0u) {
                append(&line, "ERR "); number(&line, view->receiver_error, 10u, 1u);
            } else if ((view->flags & K10_FLAG_RECEIVER_REVISION) != 0u) {
                append(&line, "PN "); number(&line, view->receiver_part, 16u, 2u);
            } else { append(&line, "WAIT"); }
            break;
        case 5u:
            if (view->keypad_error == KEYPAD_DISABLED) { append(&line, "KEYPAD OFF"); }
            else if (view->keypad_error != KEYPAD_OK) {
                append(&line, "KEYPAD ERR "); number(&line, view->keypad_error, 10u, 1u);
            } else {
                append(&line, "KEY "); append(&line, keypad_label(view->key));
                append(&line, " RAW ");
                if (view->raw_key == KEYPAD_NONE) { append(&line, "--"); }
                else { number(&line, view->raw_key, 10u, 1u); }
            }
            break;
        case 6u:
            if (view->keypad_error == KEYPAD_DISABLED) { append(&line, "PA14 LEFT UNCHANGED"); }
            else if (view->keypad_error != KEYPAD_OK) { append(&line, "INPUT UNAVAILABLE"); }
            else {
                append(&line, view->ptt ? "PTT ON  EVT " : "PTT OFF EVT ");
                number(&line, view->key_events, 10u, 1u);
            }
            break;
        default:
            if (view->stage != K10_STAGE_READY) {
                append(&line, "BOOT STAGE "); number(&line, view->stage, 10u, 1u);
                break;
            }
            append(&line, "UP "); number(&line, view->uptime_ms / 1000u, 10u, 1u);
            append(&line, "S LCD E"); number(&line, view->display_error, 10u, 1u);
            break;
        }
        draw_line(framebuffer, page, &line, page == 0u);
    }
    return true;
}
