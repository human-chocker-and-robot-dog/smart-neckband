#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define V0_VOICE_WAKE_PHRASE_UTF8 \
    "\xE4\xB8\xBB\xE4\xBA\xBA\xE4\xB8\xBB\xE4\xBA\xBA"

esp_err_t v0_voice_wakenet_start(void);
bool v0_voice_wakenet_feed(const int16_t *pcm, size_t sample_count);
const char *v0_voice_wakenet_model_name(void);

#ifdef __cplusplus
}
#endif
