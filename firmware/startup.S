/* SPDX-License-Identifier: MIT */
.syntax unified
.cpu cortex-m0plus
.thumb

.section .isr_vector,"a",%progbits
.balign 256
.global _vector_table
.type _vector_table, %object
_vector_table:
    .word _stack_top
    .word Reset_Handler
    .word NMI_Handler
    .word HardFault_Handler
    .word 0, 0, 0, 0, 0, 0, 0
    .word SVC_Handler
    .word 0, 0
    .word PendSV_Handler
    .word SysTick_Handler
    .word WWDG_IRQHandler
    .word PVD_IRQHandler
    .word RTC_IRQHandler
    .word FLASH_IRQHandler
    .word RCC_CTC_IRQHandler
    .word EXTI0_1_IRQHandler
    .word EXTI2_3_IRQHandler
    .word EXTI4_15_IRQHandler
    .word LCD_IRQHandler
    .word DMA1_Channel1_IRQHandler
    .word DMA1_Channel2_3_IRQHandler
    .word DMA1_Channel4_5_6_7_IRQHandler
    .word ADC_COMP_IRQHandler
    .word TIM1_BRK_UP_TRG_COM_IRQHandler
    .word TIM1_CC_IRQHandler
    .word TIM2_IRQHandler
    .word TIM3_IRQHandler
    .word TIM6_LPTIM1_DAC_IRQHandler
    .word TIM7_IRQHandler
    .word TIM14_IRQHandler
    .word TIM15_IRQHandler
    .word TIM16_IRQHandler
    .word TIM17_IRQHandler
    .word I2C1_IRQHandler
    .word I2C2_IRQHandler
    .word SPI1_IRQHandler
    .word SPI2_IRQHandler
    .word USART1_IRQHandler
    .word USART2_IRQHandler
    .word USART3_4_IRQHandler
    .word 0                         /* IRQ 30 is reserved on PY32F071. */
    .word USB_IRQHandler
.size _vector_table, . - _vector_table

.section .text.Reset_Handler,"ax",%progbits
.global Reset_Handler
.type Reset_Handler, %function
.thumb_func
Reset_Handler:
    cpsid i
    ldr r0, =_stack_top
    msr msp, r0
    movs r0, #0
    msr control, r0
    isb

    /* Discard interrupt enables/pending state inherited from the loader. */
    ldr r1, =0xe000e010             /* SysTick CTRL */
    str r0, [r1]
    ldr r1, =0xe000ed10             /* SCB SCR: normal sleep on WFI. */
    str r0, [r1]
    ldr r1, =0xe000e180             /* NVIC ICER0 */
    mvns r0, r0
    str r0, [r1]
    ldr r1, =0xe000e280             /* NVIC ICPR0 */
    str r0, [r1]
    ldr r1, =0xe000ed04             /* SCB ICSR: clear PendSV/SysTick */
    ldr r0, =0x0a000000
    str r0, [r1]
    ldr r1, =0xe000ed08             /* PY32F071 implements VTOR. */
    ldr r0, =_vector_table
    str r0, [r1]
    dsb
    isb

    ldr r0, =_data_load
    ldr r1, =_data_start
    ldr r2, =_data_end
1:
    cmp r1, r2
    bhs 2f
    ldr r3, [r0]
    str r3, [r1]
    adds r0, #4
    adds r1, #4
    b 1b
2:
    ldr r1, =_bss_start
    ldr r2, =_bss_end
    movs r3, #0
3:
    cmp r1, r2
    bhs 4f
    str r3, [r1]
    adds r1, #4
    b 3b
4:
    bl hal_early_init
    cpsie i
    bl main

    /* Returning from main is a diagnostic stop, not a request to reset. */
    cpsid i
    movs r0, #0
    mvns r0, r0
    movs r1, #0
    movs r2, #0
    ldr r3, =_fault_stack_top
    msr msp, r3
    bl hal_fault_trap
5:
    wfi
    b 5b
.size Reset_Handler, . - Reset_Handler
.ltorg

.section .text.Default_Handler,"ax",%progbits
.global Default_Handler
.type Default_Handler, %function
.thumb_func
Default_Handler:
    cpsid i
    mrs r0, ipsr
    mov r1, lr
    movs r3, #4
    tst r1, r3
    beq 1f
    mrs r2, psp
    b 2f
1:
    mrs r2, msp
2:
    ldr r3, =_fault_stack_top
    msr msp, r3
    bl hal_fault_trap
3:
    wfi
    b 3b
.size Default_Handler, . - Default_Handler
.ltorg

.macro weak_handler name
    .weak \name
    .thumb_set \name, Default_Handler
.endm

weak_handler NMI_Handler
weak_handler HardFault_Handler
weak_handler SVC_Handler
weak_handler PendSV_Handler
weak_handler SysTick_Handler
weak_handler WWDG_IRQHandler
weak_handler PVD_IRQHandler
weak_handler RTC_IRQHandler
weak_handler FLASH_IRQHandler
weak_handler RCC_CTC_IRQHandler
weak_handler EXTI0_1_IRQHandler
weak_handler EXTI2_3_IRQHandler
weak_handler EXTI4_15_IRQHandler
weak_handler LCD_IRQHandler
weak_handler DMA1_Channel1_IRQHandler
weak_handler DMA1_Channel2_3_IRQHandler
weak_handler DMA1_Channel4_5_6_7_IRQHandler
weak_handler ADC_COMP_IRQHandler
weak_handler TIM1_BRK_UP_TRG_COM_IRQHandler
weak_handler TIM1_CC_IRQHandler
weak_handler TIM2_IRQHandler
weak_handler TIM3_IRQHandler
weak_handler TIM6_LPTIM1_DAC_IRQHandler
weak_handler TIM7_IRQHandler
weak_handler TIM14_IRQHandler
weak_handler TIM15_IRQHandler
weak_handler TIM16_IRQHandler
weak_handler TIM17_IRQHandler
weak_handler I2C1_IRQHandler
weak_handler I2C2_IRQHandler
weak_handler SPI1_IRQHandler
weak_handler SPI2_IRQHandler
weak_handler USART1_IRQHandler
weak_handler USART2_IRQHandler
weak_handler USART3_4_IRQHandler
weak_handler USB_IRQHandler

.section .note.GNU-stack,"",%progbits
