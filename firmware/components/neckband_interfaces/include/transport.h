#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    bool connected;
    bool subscribed;
    bool congested;
    uint8_t queue_usage_percent;
    uint16_t connection_interval_units;
    uint32_t queue_overflow_count;
    uint32_t disconnected_drop_count;
    uint32_t write_error_count;
} v0_transport_status_t;

typedef void (*v0_transport_rx_callback_t)(const uint8_t *data, size_t length);

esp_err_t v0_transport_start(void);
bool v0_transport_enqueue(const uint8_t *data, size_t length);
bool v0_transport_enqueue_low_priority(const uint8_t *data, size_t length);
void v0_transport_set_rx_callback(v0_transport_rx_callback_t callback);
void v0_transport_get_status(v0_transport_status_t *out_status);

#ifdef __cplusplus
}
#endif
