#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define V0_VOICE_AUDIO_FRAME_SAMPLES 1600U
#define V0_VOICE_AUDIO_PREROLL_SAMPLES 16000U

typedef void (*v0_voice_audio_frame_callback_t)(const int16_t *pcm,
                                                 size_t sample_count);

esp_err_t v0_voice_audio_start(void);
void v0_voice_audio_set_frame_callback(v0_voice_audio_frame_callback_t callback);
size_t v0_voice_audio_copy_preroll(int16_t *out_samples, size_t capacity);
uint32_t v0_voice_audio_overflow_count(void);

#ifdef __cplusplus
}
#endif
