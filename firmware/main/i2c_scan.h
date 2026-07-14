#pragma once

#include <stdbool.h>

#include "driver/i2c_master.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"

#ifdef __cplusplus
extern "C" {
#endif

esp_err_t v0_i2c_bus_init(void);
i2c_master_bus_handle_t v0_i2c_bus_get(void);
bool v0_i2c_lock(TickType_t timeout_ticks);
void v0_i2c_unlock(void);
esp_err_t v0_i2c_scan_once(void);

#ifdef __cplusplus
}
#endif
