#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    bool sampling_active;
    bool mpu6050_online;
    uint8_t lead_off_flags;
    uint32_t missed_timer_notifications;
    uint32_t adc_error_count;
    uint32_t i2c_error_count;
} v0_sensor_status_t;

esp_err_t v0_sensors_start(void);
esp_err_t v0_sensors_set_active(bool active);
void v0_sensors_get_status(v0_sensor_status_t *out_status);

#ifdef __cplusplus
}
#endif
