/* SPDX-License-Identifier: MIT */
/*
 * The vendor application begins after an 8 KiB resident prefix. The physical
 * device is consistent with 128 KiB flash / 16 KiB RAM. Reserve the last 4 KiB
 * of flash until the nonvolatile/calibration layout is established. This is
 * deliberately smaller than the official 8.02.16 application's footprint.
 */
ENTRY(Reset_Handler)
EXTERN(_vector_table)

MEMORY
{
    FLASH (rx)  : ORIGIN = 0x08002000, LENGTH = 116K
    RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 16K
}

_stack_size = 2048;
_stack_limit = ORIGIN(RAM) + LENGTH(RAM) - _stack_size;

SECTIONS
{
    .isr_vector ORIGIN(FLASH) :
    {
        _vector_start = .;
        KEEP(*(.isr_vector))
        _vector_end = .;
    } > FLASH

    .text :
    {
        . = ALIGN(4);
        *(.text .text.*)
        KEEP(*(.firmware_info))
        *(.rodata .rodata.*)
        . = ALIGN(4);
    } > FLASH

    .ARM.extab :
    {
        *(.ARM.extab .ARM.extab.*)
    } > FLASH

    .ARM.exidx :
    {
        __exidx_start = .;
        *(.ARM.exidx .ARM.exidx.*)
        __exidx_end = .;
    } > FLASH

    .data :
    {
        . = ALIGN(4);
        _data_start = .;
        *(.data .data.*)
        . = ALIGN(4);
        _data_end = .;
    } > RAM AT > FLASH
    _data_load = LOADADDR(.data);
    _flash_image_end = LOADADDR(.data) + SIZEOF(.data);

    .bss (NOLOAD) :
    {
        . = ALIGN(4);
        _bss_start = .;
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(4);
        _bss_end = .;
    } > RAM

    .noinit (NOLOAD) :
    {
        . = ALIGN(4);
        KEEP(*(.noinit .noinit.*))
        . = ALIGN(8);
    } > RAM

    /* A fault handler must not overwrite the stack frame it is recording. */
    .fault_stack (NOLOAD) :
    {
        . = ALIGN(8);
        _fault_stack_bottom = .;
        . += 256;
        _fault_stack_top = .;
        _ram_used_end = .;
    } > RAM

    .stack _stack_limit (NOLOAD) :
    {
        _stack_bottom = .;
        . += _stack_size;
        _stack_top = .;
    } > RAM

    .ARM.attributes 0 : { *(.ARM.attributes) }
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}

ASSERT(ADDR(.isr_vector) == 0x08002000, "application vector base changed")
ASSERT(SIZEOF(.isr_vector) == (48 * 4), "expected 48 vector entries")
ASSERT((ADDR(.isr_vector) & 0xff) == 0, "vector table is not 256-byte aligned")
ASSERT(_flash_image_end <= 0x0801f000, "application overlaps reserved flash")
ASSERT(_ram_used_end <= _stack_limit, "static RAM overlaps the 2 KiB stack")
ASSERT((_stack_top & 7) == 0, "stack violates the ARM ABI alignment")
ASSERT(_stack_top == 0x20004000, "stack exceeds the 16 KiB RAM window")
ASSERT((_data_start & 3) == 0 && (_data_end & 3) == 0,
       "startup requires word-aligned initialized data")
ASSERT((_data_load & 3) == 0, "startup requires a word-aligned data load image")
ASSERT((_bss_start & 3) == 0 && (_bss_end & 3) == 0,
       "startup requires word-aligned BSS")
