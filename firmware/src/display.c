/* SPDX-License-Identifier: MIT
 * Independent K10 Pro display driver, based on the interface observations in
 * docs/display.md. No vendor code, font, or firmware image is included. */
#include "k10/display.h"
#include "k10/board.h"
#include "k10/hal.h"
#include "k10/mmio.h"

static enum display_error last_error =
    K10_ENABLE_DISPLAY ? DISPLAY_NOT_INITIALIZED : DISPLAY_DISABLED;

#if K10_ENABLE_DISPLAY

#define GPIO_MODER 0x00u
#define GPIO_OTYPER 0x04u
#define GPIO_OSPEEDR 0x08u
#define GPIO_PUPDR 0x0cu
#define GPIO_BSRR 0x18u
#define GPIO_AFRL 0x20u
#define GPIO_BRR 0x28u
#define RCC_APBRSTR1 0x2cu
#define RCC_APBRSTR2 0x30u
#define RCC_IOPENR 0x34u
#define RCC_APBENR1 0x3cu
#define RCC_APBENR2 0x40u
#define RCC_SPI1_BIT (UINT32_C(1) << 12)
#define RCC_TIM1_BIT (UINT32_C(1) << 11)
#define RCC_TIM2_TIM3_BITS UINT32_C(3)
#define SPI_CR1 0x00u
#define SPI_CR2 0x04u
#define SPI_SR 0x08u
#define SPI_DR 0x0cu
#define SPI_I2SCFGR 0x1cu
#define SPI_RXNE UINT32_C(0x01)
#define SPI_TXE UINT32_C(0x02)
#define SPI_FAULTS UINT32_C(0x70)
#define SPI_BSY UINT32_C(0x80)
#define TIM_CR1 0x00u
#define TIM_EGR 0x14u
#define TIM_CCMR1 0x18u
#define TIM_CCMR2 0x1cu
#define TIM_CCER 0x20u
#define TIM_PSC 0x28u
#define TIM_ARR 0x2cu
#define TIM_CCR1 0x34u
#define TIM_CCR2 0x38u
#define TIM_CCR3 0x3cu
#define TIM_BDTR 0x44u
#define TRANSFER_TIMEOUT_MS 10u
#define POLL_LIMIT 100000u

static bool ready;
static bool pwm_ready;

static bool fail(enum display_error error)
{
    last_error = error;
    return false;
}

static void cs(bool selected)
{
    mmio_write32(K10_GPIOB_BASE + (selected ? GPIO_BRR : GPIO_BSRR),
                 UINT32_C(1) << K10_LCD_CS_PIN);
}

static void dc(bool data)
{
    mmio_write32(K10_GPIOF_BASE + (data ? GPIO_BSRR : GPIO_BRR),
                 UINT32_C(1) << K10_LCD_DC_PIN);
}

static void set_pwm(uint8_t r, uint8_t g, uint8_t b)
{
    mmio_write32(K10_TIM1_BASE + TIM_CCR3, (uint32_t)r * 25u);
    mmio_write32(K10_TIM2_BASE + TIM_CCR1, (uint32_t)g * 25u);
    mmio_write32(K10_TIM3_BASE + TIM_CCR2, (uint32_t)b * 25u);
}

static bool wait_status(uint32_t mask, bool set)
{
    uint32_t started = hal_millis();
    for (uint32_t attempts = 0; attempts < POLL_LIMIT; ++attempts) {
        uint32_t status = mmio_read32(K10_SPI1_BASE + SPI_SR);
        if ((status & SPI_FAULTS) != 0u)
            return fail(DISPLAY_BUS_ERROR);
        if (((status & mask) != 0u) == set)
            return true;
        if ((uint32_t)(hal_millis() - started) >= TRANSFER_TIMEOUT_MS)
            break;
    }
    return fail(DISPLAY_TIMEOUT);
}

static bool drain_receive(void)
{
    uint32_t started = hal_millis();
    for (uint32_t attempts = 0; attempts < POLL_LIMIT; ++attempts) {
        uint32_t status = mmio_read32(K10_SPI1_BASE + SPI_SR);
        if ((status & SPI_FAULTS) != 0u)
            return fail(DISPLAY_BUS_ERROR);
        if ((status & SPI_RXNE) == 0u)
            return true;
        (void)mmio_read8(K10_SPI1_BASE + SPI_DR);
        if ((uint32_t)(hal_millis() - started) >= TRANSFER_TIMEOUT_MS)
            break;
    }
    return fail(DISPLAY_TIMEOUT);
}

static bool byte_out(uint8_t value)
{
    if (!wait_status(SPI_TXE, true))
        return false;
    mmio_write8(K10_SPI1_BASE + SPI_DR, value);
    if (!wait_status(SPI_RXNE, true))
        return false;
    /* Full-duplex SPI must drain RX even though the LCD is write-only. */
    (void)mmio_read8(K10_SPI1_BASE + SPI_DR);
    return true;
}

static bool finish(bool success)
{
    if (success)
        success = wait_status(SPI_TXE, true) && wait_status(SPI_BSY, false);
    cs(false);
    if (!success) {
        ready = false;
        if (pwm_ready)
            set_pwm(0u, 0u, 0u);
    }
    return success;
}

static bool begin(void)
{
    /* Drain only while deselected. This also bounds a stale RXNE condition. */
    if (!wait_status(SPI_BSY, false) || !drain_receive())
        return finish(false);
    cs(true);
    return true;
}

static void gpio_pin(uintptr_t base, unsigned pin, unsigned mode,
                     unsigned speed, unsigned pull, unsigned af)
{
    uint32_t shift = 2u * pin;
    mmio_modify32(base + GPIO_OTYPER, UINT32_C(1) << pin, 0u);
    mmio_modify32(base + GPIO_OSPEEDR, UINT32_C(3) << shift, (uint32_t)speed << shift);
    mmio_modify32(base + GPIO_PUPDR, UINT32_C(3) << shift, (uint32_t)pull << shift);
    if (mode == 2u) {
        uint32_t af_shift = 4u * (pin & 7u);
        uintptr_t af_register = base + GPIO_AFRL + 4u * (pin / 8u);
        mmio_modify32(af_register, UINT32_C(15) << af_shift, (uint32_t)af << af_shift);
    }
    mmio_modify32(base + GPIO_MODER, UINT32_C(3) << shift, (uint32_t)mode << shift);
}

static void timer_start(uintptr_t timer, uintptr_t ccmr_offset,
                        uint32_t mode, uint32_t channel_enable)
{
    /* These timers have just been reset. No IRQ/DMA is enabled. */
    mmio_write32(timer + TIM_PSC, 15u);
    mmio_write32(timer + TIM_ARR, 2500u);
    mmio_write32(timer + ccmr_offset, mode);
    mmio_write32(timer + TIM_CCER, channel_enable);
    mmio_write32(timer + TIM_EGR, 1u); /* Load the prescaler. */
    if (timer == K10_TIM1_BASE)
        mmio_write32(timer + TIM_BDTR, UINT32_C(0x8000));
    mmio_write32(timer + TIM_CR1, 1u);
}

static void initialize_pwm(void)
{
    mmio_modify32(K10_RCC_BASE + RCC_APBENR1, 0u, RCC_TIM2_TIM3_BITS);
    mmio_modify32(K10_RCC_BASE + RCC_APBENR2, 0u, RCC_TIM1_BIT);
    (void)mmio_read32(K10_RCC_BASE + RCC_APBENR2);
    mmio_modify32(K10_RCC_BASE + RCC_APBRSTR1, 0u, RCC_TIM2_TIM3_BITS);
    mmio_modify32(K10_RCC_BASE + RCC_APBRSTR1, RCC_TIM2_TIM3_BITS, 0u);
    mmio_modify32(K10_RCC_BASE + RCC_APBRSTR2, 0u, RCC_TIM1_BIT);
    mmio_modify32(K10_RCC_BASE + RCC_APBRSTR2, RCC_TIM1_BIT, 0u);
    set_pwm(0u, 0u, 0u);
    /* Only the three backlight pins are assigned to the timer alternate functions. */
    gpio_pin(K10_GPIOB_BASE, K10_BACKLIGHT_R_PIN, 2u, 2u, 0u, K10_BACKLIGHT_R_AF);
    gpio_pin(K10_GPIOA_BASE, K10_BACKLIGHT_G_PIN, 2u, 2u, 0u, K10_BACKLIGHT_G_AF);
    gpio_pin(K10_GPIOB_BASE, K10_BACKLIGHT_B_PIN, 2u, 2u, 0u, K10_BACKLIGHT_B_AF);
    timer_start(K10_TIM1_BASE, TIM_CCMR2, UINT32_C(0x60), UINT32_C(0x100));
    timer_start(K10_TIM2_BASE, TIM_CCMR1, UINT32_C(0x60), UINT32_C(0x01));
    timer_start(K10_TIM3_BASE, TIM_CCMR1, UINT32_C(0x6000), UINT32_C(0x10));
    pwm_ready = true;
}

static bool send_pages(unsigned first, unsigned count, const uint8_t *framebuffer)
{
    if (!begin())
        return false;
    dc(false);
    bool ok = byte_out(0x40u);
    for (unsigned page = first; page < first + count && ok; ++page) {
        dc(false);
        ok = byte_out((uint8_t)(0xb0u + page)) &&
             byte_out((uint8_t)(0x10u | (K10_LCD_COLUMN_OFFSET >> 4))) &&
             byte_out((uint8_t)(K10_LCD_COLUMN_OFFSET & 15u));
        if (ok)
            dc(true);
        for (unsigned x = 0; x < DISPLAY_WIDTH && ok; ++x)
            ok = byte_out(framebuffer == NULL ? 0u : framebuffer[page * DISPLAY_WIDTH + x]);
    }
    return finish(ok);
}

bool display_init(void)
{
    static const uint8_t setup[] = {0xa2u, 0xc0u, 0xa1u, 0xa6u, 0xa4u,
                                     0x24u, 0x81u, 0x1fu, 0x2bu};
    ready = false;
    pwm_ready = false;
    mmio_modify32(K10_RCC_BASE + RCC_IOPENR, 0u, UINT32_C(0x23)); /* GPIO A/B/F */
    (void)mmio_read32(K10_RCC_BASE + RCC_IOPENR);
    cs(false);
    dc(true);
    gpio_pin(K10_GPIOB_BASE, K10_LCD_CS_PIN, 1u, 3u, 1u, 0u);
    gpio_pin(K10_GPIOF_BASE, K10_LCD_DC_PIN, 1u, 3u, 1u, 0u);
    gpio_pin(K10_GPIOA_BASE, K10_LCD_SCK_PIN, 2u, 2u, 1u, K10_LCD_SCK_AF);
    gpio_pin(K10_GPIOA_BASE, K10_LCD_MOSI_PIN, 2u, 2u, 0u, K10_LCD_MOSI_AF);
    initialize_pwm();

    mmio_modify32(K10_RCC_BASE + RCC_APBENR2, 0u, RCC_SPI1_BIT);
    (void)mmio_read32(K10_RCC_BASE + RCC_APBENR2);
    mmio_modify32(K10_RCC_BASE + RCC_APBRSTR2, 0u, RCC_SPI1_BIT);
    mmio_modify32(K10_RCC_BASE + RCC_APBRSTR2, RCC_SPI1_BIT, 0u);
    mmio_write32(K10_SPI1_BASE + SPI_CR2, 0u);
    mmio_write32(K10_SPI1_BASE + SPI_I2SCFGR, 0u);
    mmio_write32(K10_SPI1_BASE + SPI_CR1, UINT32_C(0x031f));
    mmio_write32(K10_SPI1_BASE + SPI_CR1, UINT32_C(0x035f));
    if (!begin())
        return false;
    dc(false);
    bool ok = byte_out(0xe2u);
    if (ok)
        hal_delay_ms(120u);
    for (size_t i = 0; i < sizeof(setup) && ok; ++i)
        ok = byte_out(setup[i]);
    if (ok) {
        hal_delay_ms(1u);
        ok = byte_out(0x2eu);
    }
    if (ok)
        hal_delay_ms(1u);
    for (unsigned i = 0; i < 4u && ok; ++i)
        ok = byte_out(0x2fu);
    if (ok) {
        hal_delay_ms(40u);
        ok = byte_out(0x40u) && byte_out(0xafu);
    }
    if (!finish(ok) || !send_pages(0u, 1u, NULL) || !send_pages(1u, 7u, NULL))
        return false;
    ready = true;
    last_error = DISPLAY_OK;
    return true;
}

bool display_present(const uint8_t *framebuffer, size_t length)
{
    if (framebuffer == NULL || length != DISPLAY_FRAMEBUFFER_SIZE)
        return fail(DISPLAY_INVALID_ARGUMENT);
    if (!ready)
        return fail(DISPLAY_NOT_INITIALIZED);
    if (!send_pages(0u, 1u, framebuffer) || !send_pages(1u, 7u, framebuffer))
        return false;
    last_error = DISPLAY_OK;
    return true;
}

bool display_set_backlight(uint8_t r, uint8_t g, uint8_t b)
{
    if (r > 100u || g > 100u || b > 100u)
        return fail(DISPLAY_INVALID_ARGUMENT);
    if (!ready)
        return fail(DISPLAY_NOT_INITIALIZED);
    set_pwm(r, g, b);
    last_error = DISPLAY_OK;
    return true;
}

#else

bool display_init(void)
{
    last_error = DISPLAY_DISABLED;
    return false;
}

bool display_present(const uint8_t *framebuffer, size_t length)
{
    (void)framebuffer;
    (void)length;
    last_error = DISPLAY_DISABLED;
    return false;
}

bool display_set_backlight(uint8_t r, uint8_t g, uint8_t b)
{
    (void)r;
    (void)g;
    (void)b;
    last_error = DISPLAY_DISABLED;
    return false;
}

#endif

enum display_error display_last_error(void)
{
    return last_error;
}
