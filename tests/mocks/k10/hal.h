/* SPDX-License-Identifier: MIT */
#ifndef K10_HAL_H
#define K10_HAL_H
#include <stdint.h>
uint32_t hal_millis(void);
void hal_delay_us(uint32_t microseconds);
#endif
