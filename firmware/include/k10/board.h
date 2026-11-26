/* SPDX-License-Identifier: MIT */
#ifndef K10_BOARD_H
#define K10_BOARD_H

#include <stdint.h>

/* UV-K10 Pro profile: owner-confirmed PY32F071 / 25Q32 family, with GPIO
 * assignments recovered from K10PRO_ENG_v8.02.16. No physical validation here.
 * See docs/model-identity.md and docs/pinmap.md for source boundaries. */
#define K10_RCC_BASE          UINT32_C(0x40021000)
#define K10_GPIOA_BASE        UINT32_C(0x50000000)
#define K10_GPIOB_BASE        UINT32_C(0x50000400)
#define K10_GPIOC_BASE        UINT32_C(0x50000800)
#define K10_GPIOF_BASE        UINT32_C(0x50001400)
#define K10_SPI1_BASE         UINT32_C(0x40013000)
#define K10_SPI2_BASE         UINT32_C(0x40003800)
#define K10_TIM1_BASE         UINT32_C(0x40012c00)
#define K10_TIM2_BASE         UINT32_C(0x40000000)
#define K10_TIM3_BASE         UINT32_C(0x40000400)

/* 25Q32: 32 Mbit = 4,194,304 bytes. This expected board geometry does not
 * replace the read-only driver's independent runtime JEDEC identification. */
#define K10_NOR_EXPECTED_CAPACITY_BYTES UINT32_C(0x00400000)

#define K10_NOR_SCK_PIN       0u
#define K10_NOR_SCK_AF        8u
#define K10_NOR_MISO_PIN      2u
#define K10_NOR_MISO_AF       9u
#define K10_NOR_MOSI_PIN      1u
#define K10_NOR_MOSI_AF       9u
#define K10_NOR_CS_PIN        3u

#define K10_LCD_SCK_PIN       5u /* PA5 */
#define K10_LCD_SCK_AF        0u
#define K10_LCD_MOSI_PIN      7u /* PA7 */
#define K10_LCD_MOSI_AF       0u
#define K10_LCD_CS_PIN        2u /* PB2, active low */
#define K10_LCD_DC_PIN        2u /* PF2, low command / high data */
#define K10_LCD_COLUMN_OFFSET 4u

#define K10_BACKLIGHT_R_PIN   6u  /* PB6 -> TIM1_CH3, color naming from settings */
#define K10_BACKLIGHT_R_AF    11u
#define K10_BACKLIGHT_G_PIN   15u /* PA15 -> TIM2_CH1 */
#define K10_BACKLIGHT_G_AF    2u
#define K10_BACKLIGHT_B_PIN   5u  /* PB5 -> TIM3_CH2 */
#define K10_BACKLIGHT_B_AF    1u

#define K10_KEY_ROW0_PIN      8u  /* PF8 */
#define K10_KEY_ROW1_PIN      4u  /* PB4 */
#define K10_KEY_ROW2_PIN      3u  /* PB3 */
#define K10_KEY_ROW3_PIN      14u /* PA14, also SWCLK */
#define K10_KEY_COLUMNS_B    UINT32_C(0xf000) /* PB15..PB12 */
#define K10_KEY_COLUMN_A_PIN  8u
#define K10_KEY_PTT_PIN       13u /* PC13, active low; input only */

#define K10_SI4732_SCL_PIN    8u
#define K10_SI4732_SDA_PIN    9u
#define K10_SI4732_ADDRESS    UINT8_C(0x11)

/* The reset, power, audio-routing and RF transmit pins are not established.
 * No definitions or guessed GPIO operations for those signals belong here. */

#ifndef K10_ENABLE_SPI_NOR
#define K10_ENABLE_SPI_NOR 1
#endif

#ifndef K10_ENABLE_SI4732
#define K10_ENABLE_SI4732 1
#endif

#ifndef K10_ENABLE_DISPLAY
#define K10_ENABLE_DISPLAY 1
#endif

/* Scanning the last matrix row makes PA14 a GPIO output, taking it from SWCLK.
 * Keep the debugger-oriented default build's SWD clock pin available. */
#ifndef K10_ENABLE_KEYPAD
#define K10_ENABLE_KEYPAD 0
#endif

#if (K10_ENABLE_SPI_NOR != 0) && (K10_ENABLE_SPI_NOR != 1)
#error "K10_ENABLE_SPI_NOR must be 0 or 1"
#endif
#if (K10_ENABLE_SI4732 != 0) && (K10_ENABLE_SI4732 != 1)
#error "K10_ENABLE_SI4732 must be 0 or 1"
#endif
#if (K10_ENABLE_DISPLAY != 0) && (K10_ENABLE_DISPLAY != 1)
#error "K10_ENABLE_DISPLAY must be 0 or 1"
#endif
#if (K10_ENABLE_KEYPAD != 0) && (K10_ENABLE_KEYPAD != 1)
#error "K10_ENABLE_KEYPAD must be 0 or 1"
#endif

#endif
