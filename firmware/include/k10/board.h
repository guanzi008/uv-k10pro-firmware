/* SPDX-License-Identifier: MIT */
#ifndef K10_BOARD_H
#define K10_BOARD_H

#include <stdint.h>

/* Board profile inferred from K10PRO_ENG_v8.02.16, not hardware validated. */
#define K10_RCC_BASE          UINT32_C(0x40021000)
#define K10_GPIOA_BASE        UINT32_C(0x50000000)
#define K10_GPIOB_BASE        UINT32_C(0x50000400)
#define K10_SPI2_BASE         UINT32_C(0x40003800)

#define K10_NOR_SCK_PIN       0u
#define K10_NOR_SCK_AF        8u
#define K10_NOR_MISO_PIN      1u
#define K10_NOR_MISO_AF       9u
#define K10_NOR_MOSI_PIN      2u
#define K10_NOR_MOSI_AF       9u
#define K10_NOR_CS_PIN        3u

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

#if (K10_ENABLE_SPI_NOR != 0) && (K10_ENABLE_SPI_NOR != 1)
#error "K10_ENABLE_SPI_NOR must be 0 or 1"
#endif
#if (K10_ENABLE_SI4732 != 0) && (K10_ENABLE_SI4732 != 1)
#error "K10_ENABLE_SI4732 must be 0 or 1"
#endif

#endif
