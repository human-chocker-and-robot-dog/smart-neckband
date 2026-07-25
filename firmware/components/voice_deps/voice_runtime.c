#include "voice_runtime.h"

#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "protocol_v0.h"
#include "volc_asr_protocol.h"
#include "voice_audio.h"
#include "voice_config.h"
#include "voice_link.h"
#include "voice_wakenet.h"

#define VOICE_AUDIO_QUEUE_DEPTH 4U
#define VOICE_RUNTIME_TASK_STACK 8192U
#define VOICE_RUNTIME_TASK_PRIORITY 7U
#define VOICE_WIFI_TIMEOUT_MS 15000U
#define VOICE_WEBSOCKET_TIMEOUT_MS 8000U
#define VOICE_FINAL_TIMEOUT_MS 5000U
#define VOICE_ERROR_COOLDOWN_MS 1000U
#define VOICE_MAX_STREAM_FRAMES 150U
#define VOICE_MIN_STREAM_FRAMES 10U
#define VOICE_SILENCE_FRAMES 8U
#define VOICE_SILENCE_MEAN_ABS 250U
#define VOICE_ASR_ENDPOINT \
    "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"

#define WIFI_CONNECTED_BIT BIT0
#define WEBSOCKET_CONNECTED_BIT BIT1
#define WEBSOCKET_FINAL_BIT BIT2
#define WEBSOCKET_ERROR_BIT BIT3

#define VOICE_FLAG_AUDIO_READY BIT0
#define VOICE_FLAG_MODEL_READY BIT1
#define VOICE_FLAG_WIFI_READY BIT2
#define VOICE_FLAG_ASR_CONNECTED BIT3

static const char *TAG = "v0_voice_runtime";

typedef struct {
    int16_t pcm[V0_VOICE_AUDIO_FRAME_SAMPLES];
} voice_audio_frame_t;

static QueueHandle_t s_audio_queue = NULL;
static EventGroupHandle_t s_events = NULL;
static TaskHandle_t s_task = NULL;
static esp_websocket_client_handle_t s_websocket = NULL;
static v0_voice_config_t s_config = {0};
static uint8_t s_flags = 0U;
static uint32_t s_wake_count = 0U;
static uint32_t s_asr_success_count = 0U;
static uint32_t s_asr_error_count = 0U;
static volatile bool s_streaming = false;
static volatile bool s_audio_queue_overflow = false;
static uint8_t s_response[V0_VOLC_ASR_MAX_RESPONSE_BYTES] = {0};
static size_t s_response_length = 0U;
static char s_final_text[PROTOCOL_V0_VOICE_TEXT_MAX_BYTES + 1U] = {0};
static uint16_t s_server_error = 0U;
static char s_headers[640U] = {0};
static uint8_t s_outgoing_audio[
    V0_VOLC_ASR_PREFIX_SIZE +
    V0_VOICE_AUDIO_FRAME_SAMPLES * sizeof(int16_t)] = {0};

static void publish_status(uint8_t state, uint16_t error)
{
    v0_voice_link_update_runtime(state,
                                 s_flags,
                                 error,
                                 s_wake_count,
                                 s_asr_success_count,
                                 s_asr_error_count);
}

static void fail_round(v0_voice_error_t error)
{
    ++s_asr_error_count;
    publish_status(PROTOCOL_V0_VOICE_STATE_ERROR_COOLDOWN, (uint16_t)error);
    ESP_LOGW(TAG, "voice round failed error=%u", (unsigned)error);
    vTaskDelay(pdMS_TO_TICKS(VOICE_ERROR_COOLDOWN_MS));
}

static uint32_t mean_absolute(const int16_t *pcm, size_t sample_count)
{
    uint64_t sum = 0U;
    for (size_t i = 0; i < sample_count; ++i) {
        const int32_t value = pcm[i];
        sum += (uint32_t)(value < 0 ? -value : value);
    }
    return sample_count == 0U ? 0U : (uint32_t)(sum / sample_count);
}

static bool json_has_definite(const cJSON *node)
{
    if (node == NULL) {
        return false;
    }
    if (cJSON_IsObject(node)) {
        const cJSON *definite =
            cJSON_GetObjectItemCaseSensitive(node, "definite");
        if (cJSON_IsTrue(definite)) {
            return true;
        }
    }
    const cJSON *child = node->child;
    while (child != NULL) {
        if (json_has_definite(child)) {
            return true;
        }
        child = child->next;
    }
    return false;
}

static const char *json_find_text(const cJSON *root)
{
    const cJSON *result = cJSON_GetObjectItemCaseSensitive(root, "result");
    if (cJSON_IsObject(result)) {
        const cJSON *text = cJSON_GetObjectItemCaseSensitive(result, "text");
        if (cJSON_IsString(text) && text->valuestring[0] != '\0') {
            return text->valuestring;
        }
        const cJSON *utterances =
            cJSON_GetObjectItemCaseSensitive(result, "utterances");
        if (cJSON_IsArray(utterances)) {
            for (int i = cJSON_GetArraySize(utterances) - 1; i >= 0; --i) {
                const cJSON *utterance = cJSON_GetArrayItem(utterances, i);
                text = cJSON_GetObjectItemCaseSensitive(utterance, "text");
                if (cJSON_IsString(text) && text->valuestring[0] != '\0') {
                    return text->valuestring;
                }
            }
        }
    }
    const cJSON *text = cJSON_GetObjectItemCaseSensitive(root, "text");
    return cJSON_IsString(text) && text->valuestring[0] != '\0' ?
           text->valuestring : NULL;
}

static void handle_server_frame(const uint8_t *frame, size_t frame_length)
{
    v0_volc_asr_frame_view_t view = {0};
    if (!v0_volc_asr_parse_server_frame(frame, frame_length, &view)) {
        s_server_error = V0_VOICE_ERROR_PROTOCOL;
        xEventGroupSetBits(s_events, WEBSOCKET_ERROR_BIT);
        return;
    }
    if (view.message_type == V0_VOLC_ASR_MESSAGE_ERROR) {
        s_server_error = view.error_code > UINT16_MAX ?
                         UINT16_MAX : (uint16_t)view.error_code;
        xEventGroupSetBits(s_events, WEBSOCKET_ERROR_BIT);
        return;
    }
    if (view.message_type != V0_VOLC_ASR_MESSAGE_FULL_RESPONSE ||
        view.serialization != 1U) {
        return;
    }

    cJSON *json = cJSON_ParseWithLength(
        (const char *)view.payload, view.payload_length);
    if (json == NULL) {
        s_server_error = V0_VOICE_ERROR_PROTOCOL;
        xEventGroupSetBits(s_events, WEBSOCKET_ERROR_BIT);
        return;
    }

    const char *text = json_find_text(json);
    const bool final =
        (view.flags & 2U) != 0U ||
        view.sequence < 0 ||
        json_has_definite(json);
    if (text != NULL && final) {
        const size_t text_length = strlen(text);
        if (text_length <= PROTOCOL_V0_VOICE_TEXT_MAX_BYTES) {
            memcpy(s_final_text, text, text_length + 1U);
            xEventGroupSetBits(s_events, WEBSOCKET_FINAL_BIT);
        } else {
            s_server_error = V0_VOICE_ERROR_PROTOCOL;
            xEventGroupSetBits(s_events, WEBSOCKET_ERROR_BIT);
        }
    }
    cJSON_Delete(json);
}

static void websocket_event(void *handler_args,
                            esp_event_base_t base,
                            int32_t event_id,
                            void *event_data)
{
    (void)handler_args;
    (void)base;
    esp_websocket_event_data_t *data = event_data;
    switch ((esp_websocket_event_id_t)event_id) {
    case WEBSOCKET_EVENT_CONNECTED:
        s_flags |= VOICE_FLAG_ASR_CONNECTED;
        xEventGroupSetBits(s_events, WEBSOCKET_CONNECTED_BIT);
        break;
    case WEBSOCKET_EVENT_DATA:
        if (data == NULL || data->payload_len <= 0 ||
            data->payload_len > (int)sizeof(s_response) ||
            data->payload_offset < 0 || data->data_len < 0 ||
            data->payload_offset + data->data_len > data->payload_len) {
            s_server_error = V0_VOICE_ERROR_PROTOCOL;
            xEventGroupSetBits(s_events, WEBSOCKET_ERROR_BIT);
            break;
        }
        if (data->payload_offset == 0) {
            s_response_length = (size_t)data->payload_len;
        }
        memcpy(&s_response[data->payload_offset],
               data->data_ptr,
               (size_t)data->data_len);
        if (data->payload_offset + data->data_len == data->payload_len) {
            handle_server_frame(s_response, s_response_length);
        }
        break;
    case WEBSOCKET_EVENT_ERROR:
    case WEBSOCKET_EVENT_DISCONNECTED:
        s_flags &= (uint8_t)~VOICE_FLAG_ASR_CONNECTED;
        xEventGroupSetBits(s_events, WEBSOCKET_ERROR_BIT);
        break;
    default:
        break;
    }
}

static void wifi_event(void *arg,
                       esp_event_base_t event_base,
                       int32_t event_id,
                       void *event_data)
{
    (void)arg;
    (void)event_data;
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        s_flags &= (uint8_t)~VOICE_FLAG_WIFI_READY;
        xEventGroupClearBits(s_events, WIFI_CONNECTED_BIT);
        (void)esp_wifi_connect();
    } else if (event_base == IP_EVENT &&
               event_id == IP_EVENT_STA_GOT_IP) {
        s_flags |= VOICE_FLAG_WIFI_READY;
        xEventGroupSetBits(s_events, WIFI_CONNECTED_BIT);
    }
}

static esp_err_t start_wifi(void)
{
    esp_err_t err = nvs_flash_init();
    if (err != ESP_OK) {
        return err;
    }
    err = esp_netif_init();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }
    err = esp_event_loop_create_default();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }
    if (esp_netif_create_default_wifi_sta() == NULL) {
        return ESP_ERR_NO_MEM;
    }

    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    err = esp_wifi_init(&init);
    if (err != ESP_OK) {
        return err;
    }
    err = esp_event_handler_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL);
    if (err != ESP_OK) {
        return err;
    }
    err = esp_event_handler_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL);
    if (err != ESP_OK) {
        return err;
    }

    wifi_config_t wifi = {0};
    memcpy(wifi.sta.ssid,
           s_config.wifi_ssid,
           strnlen(s_config.wifi_ssid, sizeof(wifi.sta.ssid)));
    memcpy(wifi.sta.password,
           s_config.wifi_password,
           strnlen(s_config.wifi_password, sizeof(wifi.sta.password)));
    wifi.sta.threshold.authmode =
        s_config.wifi_password[0] == '\0' ? WIFI_AUTH_OPEN : WIFI_AUTH_WPA2_PSK;
    wifi.sta.pmf_cfg.capable = true;
    wifi.sta.pmf_cfg.required = false;

    err = esp_wifi_set_mode(WIFI_MODE_STA);
    if (err == ESP_OK) {
        err = esp_wifi_set_config(WIFI_IF_STA, &wifi);
    }
    if (err == ESP_OK) {
        err = esp_wifi_start();
    }
    if (err == ESP_OK) {
        err = esp_wifi_connect();
    }
    memset(&wifi, 0, sizeof(wifi));
    return err;
}

static bool wait_for_wifi(void)
{
    const EventBits_t bits = xEventGroupWaitBits(
        s_events,
        WIFI_CONNECTED_BIT,
        pdFALSE,
        pdTRUE,
        pdMS_TO_TICKS(VOICE_WIFI_TIMEOUT_MS));
    return (bits & WIFI_CONNECTED_BIT) != 0U;
}

static void make_connection_id(char *out, size_t capacity)
{
    const uint32_t a = esp_random();
    const uint32_t b = esp_random();
    const uint32_t c = esp_random();
    const uint32_t d = esp_random();
    (void)snprintf(out,
                   capacity,
                   "%08" PRIx32 "-%04" PRIx32 "-%04" PRIx32
                   "-%04" PRIx32 "-%04" PRIx32 "%08" PRIx32,
                   a,
                   b >> 16U,
                   b & 0xffffU,
                   c >> 16U,
                   c & 0xffffU,
                   d);
}

static bool open_websocket(void)
{
    char connection_id[40U] = {0};
    make_connection_id(connection_id, sizeof(connection_id));

    if (strcmp(s_config.auth_mode, "api_key") == 0) {
        (void)snprintf(s_headers,
                       sizeof(s_headers),
                       "X-Api-Key: %s\r\n"
                       "X-Api-Resource-Id: %s\r\n"
                       "X-Api-Connect-Id: %s\r\n",
                       s_config.api_key,
                       s_config.resource_id,
                       connection_id);
    } else {
        (void)snprintf(s_headers,
                       sizeof(s_headers),
                       "X-Api-App-Key: %s\r\n"
                       "X-Api-Access-Key: %s\r\n"
                       "X-Api-Resource-Id: %s\r\n"
                       "X-Api-Connect-Id: %s\r\n",
                       s_config.app_id,
                       s_config.access_token,
                       s_config.resource_id,
                       connection_id);
    }

    xEventGroupClearBits(
        s_events,
        WEBSOCKET_CONNECTED_BIT | WEBSOCKET_FINAL_BIT | WEBSOCKET_ERROR_BIT);
    s_final_text[0] = '\0';
    s_server_error = 0U;

    const esp_websocket_client_config_t config = {
        .uri = VOICE_ASR_ENDPOINT,
        .disable_auto_reconnect = true,
        .user_context = NULL,
        .task_prio = 5,
        .task_stack = 6144,
        .buffer_size = V0_VOLC_ASR_MAX_RESPONSE_BYTES,
        .headers = s_headers,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .network_timeout_ms = VOICE_WEBSOCKET_TIMEOUT_MS,
        .ping_interval_sec = 10U,
    };
    s_websocket = esp_websocket_client_init(&config);
    if (s_websocket == NULL ||
        esp_websocket_register_events(
            s_websocket,
            WEBSOCKET_EVENT_ANY,
            websocket_event,
            NULL) != ESP_OK ||
        esp_websocket_client_start(s_websocket) != ESP_OK) {
        return false;
    }

    const EventBits_t bits = xEventGroupWaitBits(
        s_events,
        WEBSOCKET_CONNECTED_BIT | WEBSOCKET_ERROR_BIT,
        pdFALSE,
        pdFALSE,
        pdMS_TO_TICKS(VOICE_WEBSOCKET_TIMEOUT_MS));
    return (bits & WEBSOCKET_CONNECTED_BIT) != 0U &&
           (bits & WEBSOCKET_ERROR_BIT) == 0U;
}

static void close_websocket(void)
{
    s_streaming = false;
    s_flags &= (uint8_t)~VOICE_FLAG_ASR_CONNECTED;
    if (s_websocket != NULL) {
        (void)esp_websocket_client_stop(s_websocket);
        (void)esp_websocket_client_destroy(s_websocket);
        s_websocket = NULL;
    }
    memset(s_headers, 0, sizeof(s_headers));
}

static bool send_binary(const uint8_t *data, size_t length)
{
    return s_websocket != NULL &&
           length <= INT32_MAX &&
           esp_websocket_client_send_bin(
               s_websocket,
               (const char *)data,
               (int)length,
               pdMS_TO_TICKS(1000U)) == (int)length;
}

static char *make_request_json(void)
{
    cJSON *root = cJSON_CreateObject();
    cJSON *user = cJSON_AddObjectToObject(root, "user");
    cJSON *audio = cJSON_AddObjectToObject(root, "audio");
    cJSON *request = cJSON_AddObjectToObject(root, "request");
    if (root == NULL || user == NULL || audio == NULL || request == NULL) {
        cJSON_Delete(root);
        return NULL;
    }

    char uid[24U] = {0};
    (void)snprintf(uid, sizeof(uid), "neckband-%08" PRIx32, esp_random());
    cJSON_AddStringToObject(user, "uid", uid);
    cJSON_AddStringToObject(audio, "format", "pcm");
    cJSON_AddNumberToObject(audio, "rate", 16000);
    cJSON_AddNumberToObject(audio, "bits", 16);
    cJSON_AddNumberToObject(audio, "channel", 1);
    cJSON_AddStringToObject(audio, "codec", "raw");
    cJSON_AddStringToObject(request, "model_name", "bigmodel");
    cJSON_AddBoolToObject(request, "enable_punc", true);
    cJSON_AddBoolToObject(request, "enable_itn", true);
    cJSON_AddBoolToObject(request, "show_utterances", true);
    cJSON_AddNumberToObject(request, "end_window_size", 800);
    cJSON_AddNumberToObject(request, "force_to_speech_time", 1000);

    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    return json;
}

static bool send_full_request(void)
{
    char *json = make_request_json();
    if (json == NULL) {
        return false;
    }
    const size_t json_length = strlen(json);
    uint8_t *frame = malloc(V0_VOLC_ASR_PREFIX_SIZE + json_length);
    const size_t frame_length =
        frame == NULL ? 0U :
        v0_volc_asr_build_full_request(
            frame,
            V0_VOLC_ASR_PREFIX_SIZE + json_length,
            json,
            json_length);
    const bool sent = frame_length > 0U && send_binary(frame, frame_length);
    free(frame);
    cJSON_free(json);
    return sent;
}

static bool send_audio(const int16_t *pcm,
                       size_t sample_count,
                       bool final_packet)
{
    const size_t frame_length = v0_volc_asr_build_audio(
        s_outgoing_audio,
        sizeof(s_outgoing_audio),
        pcm,
        sample_count,
        final_packet);
    return frame_length > 0U &&
           send_binary(s_outgoing_audio, frame_length);
}

static bool send_preroll(void)
{
    int16_t *preroll = malloc(
        V0_VOICE_AUDIO_PREROLL_SAMPLES * sizeof(*preroll));
    if (preroll == NULL) {
        return false;
    }
    const size_t samples = v0_voice_audio_copy_preroll(
        preroll, V0_VOICE_AUDIO_PREROLL_SAMPLES);
    bool sent = samples > 0U;
    for (size_t offset = 0U; sent && offset < samples;
         offset += V0_VOICE_AUDIO_FRAME_SAMPLES) {
        const size_t remaining = samples - offset;
        const size_t chunk = remaining < V0_VOICE_AUDIO_FRAME_SAMPLES ?
                             remaining : V0_VOICE_AUDIO_FRAME_SAMPLES;
        sent = send_audio(&preroll[offset], chunk, false);
    }
    memset(preroll, 0, samples * sizeof(*preroll));
    free(preroll);
    return sent;
}

static uint64_t make_utterance_id(void)
{
    return ((uint64_t)esp_random() << 32U) ^
           (uint64_t)esp_timer_get_time();
}

static bool run_asr_round(void)
{
    publish_status(PROTOCOL_V0_VOICE_STATE_ASR_CONNECTING, 0U);
    if (!wait_for_wifi() || !open_websocket()) {
        close_websocket();
        fail_round(V0_VOICE_ERROR_WEBSOCKET);
        return false;
    }
    if (!send_full_request()) {
        close_websocket();
        fail_round(V0_VOICE_ERROR_PROTOCOL);
        return false;
    }

    xQueueReset(s_audio_queue);
    s_audio_queue_overflow = false;
    s_streaming = true;
    publish_status(PROTOCOL_V0_VOICE_STATE_STREAMING, 0U);
    if (!send_preroll()) {
        close_websocket();
        fail_round(V0_VOICE_ERROR_PROTOCOL);
        return false;
    }

    bool final_sent = false;
    unsigned silence_frames = 0U;
    for (unsigned frame_index = 0U;
         frame_index < VOICE_MAX_STREAM_FRAMES;
         ++frame_index) {
        if (s_audio_queue_overflow) {
            close_websocket();
            fail_round(V0_VOICE_ERROR_AUDIO_QUEUE);
            return false;
        }
        const EventBits_t event_bits = xEventGroupGetBits(s_events);
        if ((event_bits & WEBSOCKET_ERROR_BIT) != 0U) {
            close_websocket();
            fail_round(s_server_error == 0U ?
                       V0_VOICE_ERROR_WEBSOCKET :
                       V0_VOICE_ERROR_PROTOCOL);
            return false;
        }
        if ((event_bits & WEBSOCKET_FINAL_BIT) != 0U) {
            break;
        }

        voice_audio_frame_t audio = {0};
        if (xQueueReceive(
                s_audio_queue,
                &audio,
                pdMS_TO_TICKS(250U)) != pdTRUE) {
            close_websocket();
            fail_round(V0_VOICE_ERROR_AUDIO_QUEUE);
            return false;
        }

        const uint32_t level =
            mean_absolute(audio.pcm, V0_VOICE_AUDIO_FRAME_SAMPLES);
        silence_frames = level < VOICE_SILENCE_MEAN_ABS ?
                         silence_frames + 1U : 0U;
        const bool final_packet =
            frame_index + 1U >= VOICE_MAX_STREAM_FRAMES ||
            (frame_index + 1U >= VOICE_MIN_STREAM_FRAMES &&
             silence_frames >= VOICE_SILENCE_FRAMES);
        if (!send_audio(audio.pcm,
                        V0_VOICE_AUDIO_FRAME_SAMPLES,
                        final_packet)) {
            close_websocket();
            fail_round(V0_VOICE_ERROR_WEBSOCKET);
            return false;
        }
        if (final_packet) {
            final_sent = true;
            break;
        }
    }

    s_streaming = false;
    publish_status(PROTOCOL_V0_VOICE_STATE_WAIT_FINAL, 0U);
    if (!final_sent &&
        (xEventGroupGetBits(s_events) & WEBSOCKET_FINAL_BIT) == 0U) {
        close_websocket();
        fail_round(V0_VOICE_ERROR_FINAL_TIMEOUT);
        return false;
    }

    const EventBits_t bits = xEventGroupWaitBits(
        s_events,
        WEBSOCKET_FINAL_BIT | WEBSOCKET_ERROR_BIT,
        pdFALSE,
        pdFALSE,
        pdMS_TO_TICKS(VOICE_FINAL_TIMEOUT_MS));
    close_websocket();
    if ((bits & WEBSOCKET_FINAL_BIT) == 0U) {
        fail_round((bits & WEBSOCKET_ERROR_BIT) != 0U ?
                   V0_VOICE_ERROR_PROTOCOL :
                   V0_VOICE_ERROR_FINAL_TIMEOUT);
        return false;
    }
    const size_t text_length = strlen(s_final_text);
    if (text_length == 0U) {
        fail_round(V0_VOICE_ERROR_EMPTY_FINAL);
        return false;
    }

    const uint64_t utterance_id = make_utterance_id();
    if (!v0_voice_link_submit_final_text(
            utterance_id, (const uint8_t *)s_final_text, text_length)) {
        fail_round(V0_VOICE_ERROR_TEXT_QUEUE);
        return false;
    }
    ++s_asr_success_count;
    ESP_LOGI(TAG,
             "ASR final queued id=%016" PRIx64 " bytes=%u",
             utterance_id,
             (unsigned)text_length);
    memset(s_final_text, 0, sizeof(s_final_text));
    return true;
}

static void audio_frame(const int16_t *pcm, size_t sample_count)
{
    if (s_audio_queue == NULL || pcm == NULL ||
        sample_count != V0_VOICE_AUDIO_FRAME_SAMPLES) {
        return;
    }
    if (xQueueSend(s_audio_queue, pcm, 0U) != pdTRUE && s_streaming) {
        s_audio_queue_overflow = true;
    }
}

static void runtime_task(void *arg)
{
    (void)arg;
    const esp_err_t wifi_err = start_wifi();
    if (wifi_err != ESP_OK || !wait_for_wifi()) {
        ESP_LOGE(TAG, "Wi-Fi startup failed: %s", esp_err_to_name(wifi_err));
        publish_status(
            PROTOCOL_V0_VOICE_STATE_ERROR_COOLDOWN, V0_VOICE_ERROR_WIFI);
        vTaskDelete(NULL);
        return;
    }

    for (;;) {
        publish_status(PROTOCOL_V0_VOICE_STATE_IDLE_WAKE, 0U);
        voice_audio_frame_t audio = {0};
        if (xQueueReceive(
                s_audio_queue, &audio, pdMS_TO_TICKS(500U)) != pdTRUE) {
            continue;
        }
        if (!v0_voice_wakenet_feed(
                audio.pcm, V0_VOICE_AUDIO_FRAME_SAMPLES)) {
            continue;
        }

        ++s_wake_count;
        ESP_LOGI(TAG, "exact wake phrase detected count=%" PRIu32, s_wake_count);
        (void)run_asr_round();
        xQueueReset(s_audio_queue);
    }
}

esp_err_t v0_voice_runtime_start(void)
{
    if (s_task != NULL) {
        return ESP_OK;
    }
    if (!v0_volc_asr_protocol_self_test()) {
        publish_status(
            PROTOCOL_V0_VOICE_STATE_DISABLED, V0_VOICE_ERROR_PROTOCOL);
        return ESP_ERR_INVALID_CRC;
    }

    esp_err_t err = v0_voice_link_start();
    if (err != ESP_OK) {
        return err;
    }
    err = v0_voice_config_load(&s_config);
    if (err != ESP_OK || !v0_voice_config_is_complete(&s_config)) {
        v0_voice_config_clear(&s_config);
        publish_status(
            PROTOCOL_V0_VOICE_STATE_DISABLED, V0_VOICE_ERROR_CONFIG);
        return err == ESP_OK ? ESP_ERR_INVALID_STATE : err;
    }
    err = v0_voice_wakenet_start();
    if (err != ESP_OK) {
        v0_voice_config_clear(&s_config);
        publish_status(
            PROTOCOL_V0_VOICE_STATE_DISABLED, V0_VOICE_ERROR_MODEL);
        return err;
    }
    s_flags |= VOICE_FLAG_MODEL_READY;

    s_audio_queue = xQueueCreate(
        VOICE_AUDIO_QUEUE_DEPTH, sizeof(voice_audio_frame_t));
    s_events = xEventGroupCreate();
    if (s_audio_queue == NULL || s_events == NULL) {
        return ESP_ERR_NO_MEM;
    }
    v0_voice_audio_set_frame_callback(audio_frame);
    err = v0_voice_audio_start();
    if (err != ESP_OK) {
        publish_status(
            PROTOCOL_V0_VOICE_STATE_DISABLED, V0_VOICE_ERROR_AUDIO);
        return err;
    }
    s_flags |= VOICE_FLAG_AUDIO_READY;

    if (xTaskCreate(runtime_task,
                    "v0_voice_runtime",
                    VOICE_RUNTIME_TASK_STACK,
                    NULL,
                    VOICE_RUNTIME_TASK_PRIORITY,
                    &s_task) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG,
             "voice runtime started; endpoint=%s audio is never logged or sent over BLE",
             VOICE_ASR_ENDPOINT);
    return ESP_OK;
}
