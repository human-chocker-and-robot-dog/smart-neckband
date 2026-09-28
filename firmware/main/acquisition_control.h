#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

/* V0 types 7/8, 8-byte payload, 28-byte frame. See acquisition-control-v1.md. */
esp_err_t v0_acquisition_control_init(void);
bool v0_acquisition_control_feed(const uint8_t *data, size_t length);
void v0_acquisition_control_reset_rx(void);
void v0_acquisition_control_process(void);
bool v0_acquisition_control_self_test(void);
