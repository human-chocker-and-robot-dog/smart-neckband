#include "voice_audio.h"

#include <limits.h>
#include <stdbool.h>
#include <string.h>

#include "board_config.h"
#include "driver/i2s_std.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

static const char *TAG = "v0_voice_audio";

static i2s_chan_handle_t s_rx_channel = NULL;
static TaskHandle_t s_task_handle = NULL;
static SemaphoreHandle_t s_preroll_mutex = NULL;
static int16_t *s_preroll = NULL;
static size_t s_preroll_write_index = 0U;
static size_t s_preroll_count = 0U;
static volatile uint32_t s_overflow_count = 0U;
static v0_voice_audio_frame_callback_t s_frame_callback = NULL;

static int16_t pcm16_from_i2s(int32_t raw)
{
    const int32_t shifted = raw >> BOARD_INMP441_PCM_SHIFT;
    if (shifted > INT16_MAX) {
        return INT16_MAX;
    }
    if (shifted < INT16_MIN) {
        return INT16_MIN;
    }
    return (int16_t)shifted;
}

static void append_preroll(const int16_t *pcm, size_t sample_count)
{
    if (s_preroll == NULL || s_preroll_mutex == NULL) {
        return;
    }
    if (xSemaphoreTake(s_preroll_mutex, pdMS_TO_TICKS(5U)) != pdTRUE) {
        ++s_overflow_count;
        return;
    }
    for (size_t i = 0; i < sample_count; ++i) {
        s_preroll[s_preroll_write_index] = pcm[i];
        s_preroll_write_index =
            (s_preroll_write_index + 1U) % V0_VOICE_AUDIO_PREROLL_SAMPLES;
        if (s_preroll_count < V0_VOICE_AUDIO_PREROLL_SAMPLES) {
            ++s_preroll_count;
        }
    }
    xSemaphoreGive(s_preroll_mutex);
}

static void audio_task(void *arg)
{
    (void)arg;
    int32_t *raw = heap_caps_calloc(
        V0_VOICE_AUDIO_FRAME_SAMPLES, sizeof(int32_t), MALLOC_CAP_8BIT);
    int16_t *pcm = heap_caps_calloc(
        V0_VOICE_AUDIO_FRAME_SAMPLES, sizeof(int16_t), MALLOC_CAP_8BIT);
    if (raw == NULL || pcm == NULL) {
        ESP_LOGE(TAG, "failed to allocate audio frame buffers");
        heap_caps_free(raw);
        heap_caps_free(pcm);
        s_task_handle = NULL;
        vTaskDelete(NULL);
        return;
    }

    for (;;) {
        size_t bytes_read = 0U;
        const esp_err_t err = i2s_channel_read(
            s_rx_channel,
            raw,
            V0_VOICE_AUDIO_FRAME_SAMPLES * sizeof(int32_t),
            &bytes_read,
            pdMS_TO_TICKS(200U));
        if (err == ESP_ERR_TIMEOUT) {
            ++s_overflow_count;
            continue;
        }
        if (err != ESP_OK) {
            ++s_overflow_count;
            ESP_LOGW(TAG, "I2S read failed: %s", esp_err_to_name(err));
            vTaskDelay(pdMS_TO_TICKS(20U));
            continue;
        }

        const size_t sample_count = bytes_read / sizeof(int32_t);
        for (size_t i = 0; i < sample_count; ++i) {
            pcm[i] = pcm16_from_i2s(raw[i]);
        }
        append_preroll(pcm, sample_count);
        v0_voice_audio_frame_callback_t callback = s_frame_callback;
        if (callback != NULL) {
            callback(pcm, sample_count);
        }
    }
}

esp_err_t v0_voice_audio_start(void)
{
    if (s_task_handle != NULL) {
        return ESP_OK;
    }

    s_preroll_mutex = xSemaphoreCreateMutex();
    s_preroll = heap_caps_calloc(
        V0_VOICE_AUDIO_PREROLL_SAMPLES, sizeof(int16_t), MALLOC_CAP_8BIT);
    if (s_preroll_mutex == NULL || s_preroll == NULL) {
        return ESP_ERR_NO_MEM;
    }

    const i2s_chan_config_t channel_config =
        I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    esp_err_t err = i2s_new_channel(&channel_config, NULL, &s_rx_channel);
    if (err != ESP_OK) {
        return err;
    }

    i2s_std_config_t standard_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(BOARD_INMP441_SAMPLE_RATE_HZ),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
            I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = BOARD_INMP441_BCLK_GPIO,
            .ws = BOARD_INMP441_WS_GPIO,
            .dout = I2S_GPIO_UNUSED,
            .din = BOARD_INMP441_SD_GPIO,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };
    standard_config.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;
    err = i2s_channel_init_std_mode(s_rx_channel, &standard_config);
    if (err != ESP_OK) {
        return err;
    }
    err = i2s_channel_enable(s_rx_channel);
    if (err != ESP_OK) {
        return err;
    }

    if (xTaskCreate(audio_task,
                    "v0_voice_audio",
                    BOARD_VOICE_AUDIO_TASK_STACK_BYTES,
                    NULL,
                    BOARD_VOICE_AUDIO_TASK_PRIORITY,
                    &s_task_handle) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(TAG,
             "INMP441 started rate=%uHz bclk=GPIO%d ws=GPIO%d sd=GPIO%d shift=%d",
             BOARD_INMP441_SAMPLE_RATE_HZ,
             BOARD_INMP441_BCLK_GPIO,
             BOARD_INMP441_WS_GPIO,
             BOARD_INMP441_SD_GPIO,
             BOARD_INMP441_PCM_SHIFT);
    return ESP_OK;
}

void v0_voice_audio_set_frame_callback(v0_voice_audio_frame_callback_t callback)
{
    s_frame_callback = callback;
}

size_t v0_voice_audio_copy_preroll(int16_t *out_samples, size_t capacity)
{
    if (out_samples == NULL || capacity == 0U ||
        s_preroll == NULL || s_preroll_mutex == NULL ||
        xSemaphoreTake(s_preroll_mutex, pdMS_TO_TICKS(20U)) != pdTRUE) {
        return 0U;
    }

    const size_t copy_count = s_preroll_count < capacity ?
                              s_preroll_count : capacity;
    const size_t start =
        (s_preroll_write_index + V0_VOICE_AUDIO_PREROLL_SAMPLES - copy_count) %
        V0_VOICE_AUDIO_PREROLL_SAMPLES;
    for (size_t i = 0; i < copy_count; ++i) {
        out_samples[i] =
            s_preroll[(start + i) % V0_VOICE_AUDIO_PREROLL_SAMPLES];
    }
    xSemaphoreGive(s_preroll_mutex);
    return copy_count;
}

uint32_t v0_voice_audio_overflow_count(void)
{
    return s_overflow_count;
}
