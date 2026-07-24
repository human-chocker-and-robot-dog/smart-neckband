#include "voice_wakenet.h"

#include <stdlib.h>
#include <string.h>

#include "board_config.h"
#include "esp_log.h"
#include "esp_wn_iface.h"
#include "esp_wn_models.h"
#include "model_path.h"

static const char *TAG = "v0_wakenet";

static srmodel_list_t *s_models = NULL;
static const esp_wn_iface_t *s_iface = NULL;
static model_iface_data_t *s_model = NULL;
static const char *s_model_name = NULL;
static int16_t *s_chunk = NULL;
static size_t s_chunk_samples = 0U;
static size_t s_chunk_fill = 0U;

static const char *find_exact_model(srmodel_list_t *models)
{
    if (models == NULL) {
        return NULL;
    }
    for (int i = 0; i < models->num; ++i) {
        char *name = models->model_name[i];
        if (name == NULL || strstr(name, ESP_WN_PREFIX) == NULL) {
            continue;
        }
        char *words = esp_srmodel_get_wake_words(models, name);
        if (words != NULL && strcmp(words, V0_VOICE_WAKE_PHRASE_UTF8) == 0) {
            return name;
        }
    }
    return NULL;
}

static void release_model(void)
{
    if (s_iface != NULL && s_model != NULL) {
        s_iface->destroy(s_model);
    }
    free(s_chunk);
    if (s_models != NULL) {
        esp_srmodel_deinit(s_models);
    }
    s_models = NULL;
    s_iface = NULL;
    s_model = NULL;
    s_model_name = NULL;
    s_chunk = NULL;
    s_chunk_samples = 0U;
    s_chunk_fill = 0U;
}

esp_err_t v0_voice_wakenet_start(void)
{
    if (s_model != NULL) {
        return ESP_OK;
    }

    s_models = esp_srmodel_init("model");
    s_model_name = find_exact_model(s_models);
    if (s_model_name == NULL) {
        ESP_LOGE(TAG,
                 "model partition has no exact WakeNet phrase; expected UTF-8 "
                 "phrase bytes for configured wake word");
        release_model();
        return ESP_ERR_NOT_FOUND;
    }

    s_iface = esp_wn_handle_from_name(s_model_name);
    if (s_iface == NULL) {
        release_model();
        return ESP_ERR_NOT_SUPPORTED;
    }
    s_model = s_iface->create(s_model_name, DET_MODE_95);
    if (s_model == NULL) {
        release_model();
        return ESP_ERR_NO_MEM;
    }

    const int rate = s_iface->get_samp_rate(s_model);
    const int channels = s_iface->get_channel_num(s_model);
    const int chunk_samples = s_iface->get_samp_chunksize(s_model);
    if (rate != BOARD_INMP441_SAMPLE_RATE_HZ ||
        channels != 1 ||
        chunk_samples <= 0) {
        ESP_LOGE(TAG,
                 "unsupported model audio shape rate=%d channels=%d chunk=%d",
                 rate,
                 channels,
                 chunk_samples);
        release_model();
        return ESP_ERR_INVALID_RESPONSE;
    }

    s_chunk_samples = (size_t)chunk_samples;
    s_chunk = calloc(s_chunk_samples, sizeof(*s_chunk));
    if (s_chunk == NULL) {
        release_model();
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(TAG,
             "exact WakeNet model ready name=%s rate=%dHz chunk=%d",
             s_model_name,
             rate,
             chunk_samples);
    return ESP_OK;
}

bool v0_voice_wakenet_feed(const int16_t *pcm, size_t sample_count)
{
    if (pcm == NULL || s_model == NULL || s_chunk == NULL) {
        return false;
    }

    bool detected = false;
    size_t offset = 0U;
    while (offset < sample_count) {
        const size_t free_samples = s_chunk_samples - s_chunk_fill;
        const size_t remaining = sample_count - offset;
        const size_t copy_samples =
            remaining < free_samples ? remaining : free_samples;
        memcpy(&s_chunk[s_chunk_fill],
               &pcm[offset],
               copy_samples * sizeof(*pcm));
        s_chunk_fill += copy_samples;
        offset += copy_samples;

        if (s_chunk_fill == s_chunk_samples) {
            const wakenet_state_t state = s_iface->detect(s_model, s_chunk);
            s_chunk_fill = 0U;
            if (state == WAKENET_DETECTED) {
                detected = true;
                s_iface->clean(s_model);
            }
        }
    }
    return detected;
}

const char *v0_voice_wakenet_model_name(void)
{
    return s_model_name;
}
