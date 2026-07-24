#pragma once

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    V0_VOICE_ERROR_NONE = 0U,
    V0_VOICE_ERROR_CONFIG = 1U,
    V0_VOICE_ERROR_MODEL = 2U,
    V0_VOICE_ERROR_AUDIO = 3U,
    V0_VOICE_ERROR_WIFI = 4U,
    V0_VOICE_ERROR_WEBSOCKET = 5U,
    V0_VOICE_ERROR_PROTOCOL = 6U,
    V0_VOICE_ERROR_AUDIO_QUEUE = 7U,
    V0_VOICE_ERROR_FINAL_TIMEOUT = 8U,
    V0_VOICE_ERROR_EMPTY_FINAL = 9U,
    V0_VOICE_ERROR_TEXT_QUEUE = 10U,
} v0_voice_error_t;

esp_err_t v0_voice_runtime_start(void);

#ifdef __cplusplus
}
#endif
