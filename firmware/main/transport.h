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
    bool congested;
    uint8_t queue_usage_percent;
    uint32_t queue_overflow_count;
    uint32_t disconnected_drop_count;
    uint32_t write_error_count;
} v0_transport_status_t;

esp_err_t v0_transport_start(void);
bool v0_transport_enqueue(const uint8_t *data, size_t length);
void v0_transport_get_status(v0_transport_status_t *out_status);

#ifdef __cplusplus
}
#endif
