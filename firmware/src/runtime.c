/* SPDX-License-Identifier: MIT
 * Minimal freestanding C / ARM EABI helpers. No allocator or hosted runtime.
 */
#include <stddef.h>
#include <stdint.h>

void *memcpy(void *destination, const void *source, size_t length)
{
    unsigned char *out = destination;
    const unsigned char *in = source;
    for (size_t i = 0u; i < length; ++i) {
        out[i] = in[i];
    }
    return destination;
}

void *memset(void *destination, int value, size_t length)
{
    unsigned char *out = destination;
    for (size_t i = 0u; i < length; ++i) {
        out[i] = (unsigned char)value;
    }
    return destination;
}

void *memmove(void *destination, const void *source, size_t length)
{
    unsigned char *out = destination;
    const unsigned char *in = source;
    if ((uintptr_t)out < (uintptr_t)in) {
        for (size_t i = 0u; i < length; ++i) {
            out[i] = in[i];
        }
    } else if (out != in) {
        while (length != 0u) {
            --length;
            out[length] = in[length];
        }
    }
    return destination;
}

void __aeabi_memcpy(void *destination, const void *source, size_t length)
{
    (void)memcpy(destination, source, length);
}

void __aeabi_memcpy4(void *destination, const void *source, size_t length)
{
    (void)memcpy(destination, source, length);
}

void __aeabi_memcpy8(void *destination, const void *source, size_t length)
{
    (void)memcpy(destination, source, length);
}

void __aeabi_memset(void *destination, size_t length, int value)
{
    (void)memset(destination, value, length);
}

void __aeabi_memclr(void *destination, size_t length)
{
    (void)memset(destination, 0, length);
}

void __aeabi_memclr4(void *destination, size_t length)
{
    (void)memset(destination, 0, length);
}

void __aeabi_memclr8(void *destination, size_t length)
{
    (void)memset(destination, 0, length);
}
