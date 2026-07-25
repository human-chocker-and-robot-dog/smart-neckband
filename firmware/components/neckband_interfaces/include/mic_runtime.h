#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    MIC_RUNTIME_DISABLED = 0,
    MIC_RUNTIME_DISCONNECTED = 1,
    MIC_RUNTIME_ARMED = 2,
    MIC_RUNTIME_STREAMING = 3,
    MIC_RUNTIME_ERROR = 4,
} mic_runtime_state_t;

typedef struct {
    mic_runtime_state_t state;
    uint32_t wake_count;
    uint32_t i2s_error_count;
    uint32_t tx_error_count;
    uint32_t clipped_frame_count;
    uint32_t control_error_count;
    uint8_t pcm_shift;
} mic_runtime_status_t;

esp_err_t mic_runtime_start(void);
bool mic_runtime_protocol_self_test(void);
bool mic_runtime_handle_command(const uint8_t *data, size_t length);
void mic_runtime_record_control_error(void);
void mic_runtime_get_status(mic_runtime_status_t *out_status);

#ifdef __cplusplus
}
#endif
