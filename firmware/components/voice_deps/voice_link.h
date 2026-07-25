#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "protocol_v0.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint8_t state;
    uint8_t flags;
    uint16_t last_error;
    uint32_t wake_count;
    uint32_t asr_success_count;
    uint32_t asr_error_count;
    uint32_t text_drop_count;
    uint8_t pending_text_count;
} v0_voice_link_status_t;

esp_err_t v0_voice_link_start(void);
bool v0_voice_link_submit_final_text(uint64_t utterance_id,
                                     const uint8_t *utf8_text,
                                     size_t text_length);
void v0_voice_link_update_runtime(uint8_t state,
                                  uint8_t flags,
                                  uint16_t last_error,
                                  uint32_t wake_count,
                                  uint32_t asr_success_count,
                                  uint32_t asr_error_count);
void v0_voice_link_get_status(v0_voice_link_status_t *out_status);

#ifdef __cplusplus
}
#endif
