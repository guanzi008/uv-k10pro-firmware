# SPDX-License-Identifier: MIT
CROSS_COMPILE ?= arm-none-eabi-
PYTHON ?= python3
BUILD ?= build
VERSION ?= 0.1.0-exp

CC := $(CROSS_COMPILE)gcc
OBJCOPY := $(CROSS_COMPILE)objcopy
SIZE := $(CROSS_COMPILE)size
NM := $(CROSS_COMPILE)nm
READELF := $(CROSS_COMPILE)readelf

CPPFLAGS += -Ifirmware/include -DK10_VERSION=\"$(VERSION)\" $(EXTRA_CPPFLAGS)
ARCHFLAGS := -mcpu=cortex-m0plus -mthumb -mfloat-abi=soft
CFLAGS := $(ARCHFLAGS) -std=c11 -Os -g3 -ffreestanding -fno-builtin \
	-fno-common -fno-unwind-tables -fno-asynchronous-unwind-tables \
	-fno-tree-loop-distribute-patterns -ffunction-sections -fdata-sections \
	-fstack-usage -Wall -Wextra -Werror -Wshadow -Wundef -Wstrict-prototypes
ASFLAGS := $(ARCHFLAGS) -g3 -x assembler-with-cpp
LDFLAGS := $(ARCHFLAGS) -nostdlib -Tfirmware/linker.ld \
	-Wl,--gc-sections,--build-id=none,-z,noexecstack,-Map=$(BUILD)/k10pro.map

C_SOURCES := $(sort $(wildcard firmware/src/*.c))
OBJECTS := $(patsubst firmware/src/%.c,$(BUILD)/%.o,$(C_SOURCES)) $(BUILD)/startup.o
DEPS := $(OBJECTS:.o=.d)

define K10_BUILD_CONFIG
CC=$(CC)
CPPFLAGS=$(CPPFLAGS)
CFLAGS=$(CFLAGS)
ASFLAGS=$(ASFLAGS)
LDFLAGS=$(LDFLAGS)
endef

.PHONY: all verify test package clean help FORCE
all: $(BUILD)/k10pro.bin

$(BUILD):
	mkdir -p $@

FORCE:

# Only change the stamp when the effective compile/link configuration changes.
# GNU Make's file function writes the values without shell interpolation.
$(BUILD)/.config: FORCE | $(BUILD)
	$(file >$@.tmp,$(K10_BUILD_CONFIG))
	@cmp -s $@.tmp $@ || cp $@.tmp $@
	@rm -f $@.tmp

$(OBJECTS): $(BUILD)/.config

$(BUILD)/%.o: firmware/src/%.c | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) -MMD -MP -c $< -o $@

$(BUILD)/startup.o: firmware/startup.S | $(BUILD)
	$(CC) $(CPPFLAGS) $(ASFLAGS) -MMD -MP -c $< -o $@

$(BUILD)/k10pro.elf: $(OBJECTS) firmware/linker.ld
	$(CC) $(LDFLAGS) $(OBJECTS) -lgcc -o $@
	$(SIZE) $@

$(BUILD)/k10pro.bin: $(BUILD)/k10pro.elf
	$(OBJCOPY) -O binary $< $@

verify: $(BUILD)/k10pro.bin
	$(PYTHON) tools/verify_image.py $< > $(BUILD)/verification.json
	cat $(BUILD)/verification.json
	$(READELF) -h $(BUILD)/k10pro.elf
	@test -z "$$($(NM) -u $(BUILD)/k10pro.elf)" || { $(NM) -u $(BUILD)/k10pro.elf; exit 1; }

test:
	$(PYTHON) -m unittest discover -s tests -v
	$(PYTHON) tests/run_driver_tests.py

package: verify
	$(PYTHON) tools/fwtool.py pack $(BUILD)/k10pro.bin $(BUILD)/k10pro.experimental.bin --version $(VERSION) --force
	$(PYTHON) tools/fwtool.py inspect $(BUILD)/k10pro.experimental.bin > $(BUILD)/package.json

clean:
	rm -rf $(BUILD)

help:
	@echo 'make             Build developer diagnostic firmware (ELF + raw binary).'
	@echo 'make test        Run host-side protocol and bounds tests.'
	@echo 'make verify      Check the image layout and unresolved symbols.'
	@echo 'make package     Produce an experimental container; does not flash a device.'
	@echo 'CROSS_COMPILE=/path/to/bin/arm-none-eabi- selects your toolchain.'

-include $(DEPS)
