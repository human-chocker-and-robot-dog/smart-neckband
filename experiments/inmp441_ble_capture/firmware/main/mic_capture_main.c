#include <limits.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ble_uart.h"
#include "driver/i2s_std.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_wn_iface.h"
#include "esp_wn_models.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "host/ble_gap.h"
#include "host/ble_hs.h"
#include "model_path.h"
#include "nvs_flash.h"

#define MIC_SAMPLE_RATE_HZ 16000U
#define MIC_FRAME_SAMPLES 400U
#define MIC_BCLK_GPIO GPIO_NUM_4
#define MIC_WS_GPIO GPIO_NUM_5
#define MIC_SD_GPIO GPIO_NUM_20
#define MIC_INITIAL_SHIFT 16U
#define MIC_WAKE_COOLDOWN_SAMPLES (MIC_SAMPLE_RATE_HZ * 2U)

#define MIC_FRAME_TYPE_AUDIO 1U
#define MIC_FRAME_TYPE_STATUS 2U
#define MIC_FRAME_TYPE_WAKE 3U
#define MIC_ENCODING_PCM16 1U
#define MIC_ENCODING_PCM8 2U
#define MIC_ENCODING_IMA_ADPCM 3U

#define MIC_FLAG_CLIPPED (1U << 0)
#define MIC_FLAG_I2S_ERROR (1U << 1)
#define MIC_FLAG_TX_ERROR (1U << 2)

#define MIC_HEADER_SIZE 29U
#define MIC_CRC_SIZE 2U
#define MIC_PCM16_PAYLOAD_BYTES (MIC_FRAME_SAMPLES * sizeof(int16_t))
#define MIC_MAX_FRAME_BYTES (MIC_HEADER_SIZE + MIC_PCM16_PAYLOAD_BYTES + MIC_CRC_SIZE)
#define MIC_TX_QUEUE_DEPTH 8U

static const char *TAG = "mic_capture";
static const uint8_t MIC_MAGIC[4] = {'M', 'I', 'C', '1'};

typedef struct {
    char text[32];
} mic_command_t;

typedef struct {
    uint16_t length;
    uint8_t data[MIC_MAX_FRAME_BYTES];
} mic_tx_item_t;

static i2s_chan_handle_t s_rx_channel;
static QueueHandle_t s_command_queue;
static QueueHandle_t s_tx_queue;
static volatile bool s_streaming;
static volatile bool s_wake_armed;
static volatile uint8_t s_encoding = MIC_ENCODING_IMA_ADPCM;
static volatile uint8_t s_pcm_shift = MIC_INITIAL_SHIFT;
static volatile uint16_t s_conn_interval;
static bool s_conn_params_requested;
static bool s_was_connected;
static int s_adpcm_index;
static uint32_t s_sequence;
static uint64_t s_first_sample_index;
static uint32_t s_i2s_error_count;
static uint32_t s_tx_error_count;
static uint32_t s_clipped_frame_count;
static uint32_t s_wake_count;
static uint64_t s_total_sample_index;
static uint64_t s_last_wake_sample_index;
static srmodel_list_t *s_srmodels;
static const esp_wn_iface_t *s_wakenet;
static model_iface_data_t *s_wakenet_data;
static int16_t *s_wakenet_buffer;
static size_t s_wakenet_chunk_samples;
static size_t s_wakenet_buffered_samples;

extern int ble_gap_conn_foreach_handle(
    ble_gap_conn_foreach_handle_fn *callback, void *arg);

static const int16_t ADPCM_STEP_TABLE[89] = {
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
    34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130,
    143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449,
    494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411,
    1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026,
    4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
    11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
    27086, 29794, 32767,
};

static const int8_t ADPCM_INDEX_TABLE[16] = {
    -1, -1, -1, -1, 2, 4, 6, 8,
    -1, -1, -1, -1, 2, 4, 6, 8,
};

static void write_le16(uint8_t *out, uint16_t value)
{
    out[0] = (uint8_t)value;
    out[1] = (uint8_t)(value >> 8);
}

static void write_le32(uint8_t *out, uint32_t value)
{
    for (size_t i = 0; i < 4; ++i) {
        out[i] = (uint8_t)(value >> (8U * i));
    }
}

static void write_le64(uint8_t *out, uint64_t value)
{
    for (size_t i = 0; i < 8; ++i) {
        out[i] = (uint8_t)(value >> (8U * i));
    }
}

static uint16_t crc16_ccitt_false(const uint8_t *data, size_t length)
{
    uint16_t crc = 0xFFFFU;
    for (size_t i = 0; i < length; ++i) {
        crc ^= (uint16_t)data[i] << 8;
        for (unsigned bit = 0; bit < 8U; ++bit) {
            crc = (crc & 0x8000U) != 0U ?
                  (uint16_t)((crc << 1) ^ 0x1021U) :
                  (uint16_t)(crc << 1);
        }
    }
    return crc;
}

static uint8_t encode_adpcm_nibble(int16_t sample, int *predictor, int *index)
{
    const int step = ADPCM_STEP_TABLE[*index];
    int difference = (int)sample - *predictor;
    uint8_t code = 0U;
    if (difference < 0) {
        code = 8U;
        difference = -difference;
    }

    int delta = step >> 3;
    if (difference >= step) {
        code |= 4U;
        difference -= step;
        delta += step;
    }
    if (difference >= (step >> 1)) {
        code |= 2U;
        difference -= step >> 1;
        delta += step >> 1;
    }
    if (difference >= (step >> 2)) {
        code |= 1U;
        delta += step >> 2;
    }

    *predictor += (code & 8U) != 0U ? -delta : delta;
    if (*predictor > INT16_MAX) {
        *predictor = INT16_MAX;
    } else if (*predictor < INT16_MIN) {
        *predictor = INT16_MIN;
    }
    *index += ADPCM_INDEX_TABLE[code];
    if (*index < 0) {
        *index = 0;
    } else if (*index > 88) {
        *index = 88;
    }
    return code;
}

static uint16_t encode_ima_adpcm(const int16_t *pcm,
                                 size_t sample_count,
                                 uint8_t *payload)
{
    if (sample_count == 0U) {
        return 0U;
    }

    int predictor = pcm[0];
    int index = s_adpcm_index;
    write_le16(payload, (uint16_t)predictor);
    payload[2] = (uint8_t)index;
    payload[3] = 0U;
    memset(payload + 4U, 0, (sample_count + 1U) / 2U);

    for (size_t i = 1U; i < sample_count; ++i) {
        const uint8_t code = encode_adpcm_nibble(pcm[i], &predictor, &index);
        const size_t nibble_index = i - 1U;
        const size_t byte_index = 4U + (nibble_index / 2U);
        if ((nibble_index & 1U) == 0U) {
            payload[byte_index] = code;
        } else {
            payload[byte_index] |= (uint8_t)(code << 4);
        }
    }
    s_adpcm_index = index;
    return (uint16_t)(4U + (sample_count / 2U));
}

static int update_connection_callback(uint16_t conn_handle, void *arg)
{
    struct ble_gap_conn_desc description = {0};
    if (ble_gap_conn_find(conn_handle, &description) != 0) {
        return 0;
    }
    s_conn_interval = description.conn_itvl;
    if (*(bool *)arg && !s_conn_params_requested) {
        const struct ble_gap_upd_params parameters = {
            .itvl_min = 6U,
            .itvl_max = 12U,
            .latency = 0U,
            .supervision_timeout = 400U,
            .min_ce_len = 0U,
            .max_ce_len = 0U,
        };
        const int result = ble_gap_update_params(conn_handle, &parameters);
        if (result == 0 || result == BLE_HS_EALREADY) {
            s_conn_params_requested = true;
        } else {
            ESP_LOGW(TAG, "BLE connection parameter update failed: %d", result);
        }
    }
    return 0;
}

static void refresh_connection_parameters(bool request_update)
{
    (void)ble_gap_conn_foreach_handle(
        update_connection_callback, &request_update);
}

static size_t begin_frame(uint8_t *frame,
                          uint8_t frame_type,
                          uint8_t encoding,
                          uint16_t flags,
                          uint32_t sequence,
                          uint64_t first_sample_index,
                          uint16_t sample_count,
                          uint16_t payload_length)
{
    memcpy(frame, MIC_MAGIC, sizeof(MIC_MAGIC));
    frame[4] = 1U;
    frame[5] = frame_type;
    frame[6] = encoding;
    write_le16(frame + 7, flags);
    write_le32(frame + 9, sequence);
    write_le32(frame + 13, MIC_SAMPLE_RATE_HZ);
    write_le64(frame + 17, first_sample_index);
    write_le16(frame + 25, sample_count);
    write_le16(frame + 27, payload_length);
    return MIC_HEADER_SIZE;
}

static int16_t pcm16_from_i2s(int32_t raw, uint8_t shift, bool *clipped)
{
    const int32_t shifted = raw >> shift;
    if (shifted > INT16_MAX) {
        *clipped = true;
        return INT16_MAX;
    }
    if (shifted < INT16_MIN) {
        *clipped = true;
        return INT16_MIN;
    }
    return (int16_t)shifted;
}

static void send_status(void)
{
    refresh_connection_parameters(false);
    uint8_t frame[MIC_HEADER_SIZE + 20U + MIC_CRC_SIZE] = {0};
    const size_t payload_offset = begin_frame(
        frame,
        MIC_FRAME_TYPE_STATUS,
        s_encoding,
        0U,
        s_sequence,
        s_first_sample_index,
        0U,
        20U);
    write_le32(frame + payload_offset, s_i2s_error_count);
    write_le32(frame + payload_offset + 4U, s_tx_error_count);
    write_le32(frame + payload_offset + 8U, s_clipped_frame_count);
    write_le32(frame + payload_offset + 12U, s_conn_interval);
    write_le16(frame + payload_offset + 16U, s_pcm_shift);
    frame[payload_offset + 18U] = s_encoding;
    frame[payload_offset + 19U] =
        s_streaming ? 1U : (s_wake_armed ? 2U : 0U);
    const size_t crc_offset = payload_offset + 20U;
    write_le16(frame + crc_offset, crc16_ccitt_false(frame, crc_offset));
    if (ble_uart_tx(frame, crc_offset + MIC_CRC_SIZE) != BLE_UART_OK) {
        ++s_tx_error_count;
    }
}

static void send_wake_event(uint64_t detected_sample_index, uint16_t word_index)
{
    if (!ble_uart_is_connected() || !ble_uart_is_subscribed()) {
        return;
    }

    uint8_t frame[MIC_HEADER_SIZE + 8U + MIC_CRC_SIZE] = {0};
    const size_t payload_offset = begin_frame(
        frame,
        MIC_FRAME_TYPE_WAKE,
        s_encoding,
        0U,
        s_wake_count,
        detected_sample_index,
        0U,
        8U);
    write_le32(frame + payload_offset, s_wake_count);
    write_le16(frame + payload_offset + 4U, word_index);
    write_le16(frame + payload_offset + 6U, 0U);
    const size_t crc_offset = payload_offset + 8U;
    write_le16(frame + crc_offset, crc16_ccitt_false(frame, crc_offset));
    if (ble_uart_tx(frame, crc_offset + MIC_CRC_SIZE) != BLE_UART_OK) {
        ++s_tx_error_count;
    }
}

static void on_ble_rx(const uint8_t *data, size_t length)
{
    if (data == NULL || length == 0U || s_command_queue == NULL) {
        return;
    }
    mic_command_t command = {0};
    const size_t copy_length =
        length < sizeof(command.text) - 1U ? length : sizeof(command.text) - 1U;
    memcpy(command.text, data, copy_length);
    (void)xQueueSend(s_command_queue, &command, 0U);
}

static void reset_capture_session(void)
{
    s_sequence = 0U;
    s_first_sample_index = 0U;
    s_i2s_error_count = 0U;
    s_tx_error_count = 0U;
    s_clipped_frame_count = 0U;
    s_adpcm_index = 0;
    if (s_tx_queue != NULL) {
        (void)xQueueReset(s_tx_queue);
    }
}

static void process_wakenet(const int16_t *pcm, size_t sample_count)
{
    if (s_wakenet == NULL ||
        s_wakenet_data == NULL ||
        s_wakenet_buffer == NULL ||
        s_wakenet_chunk_samples == 0U) {
        return;
    }

    size_t offset = 0U;
    while (offset < sample_count) {
        const size_t remaining = sample_count - offset;
        const size_t capacity = s_wakenet_chunk_samples - s_wakenet_buffered_samples;
        const size_t copy_count = remaining < capacity ? remaining : capacity;
        memcpy(
            s_wakenet_buffer + s_wakenet_buffered_samples,
            pcm + offset,
            copy_count * sizeof(pcm[0]));
        offset += copy_count;
        s_wakenet_buffered_samples += copy_count;

        if (s_wakenet_buffered_samples != s_wakenet_chunk_samples) {
            continue;
        }

        const int detected_word =
            (int)s_wakenet->detect(s_wakenet_data, s_wakenet_buffer);
        s_wakenet_buffered_samples = 0U;
        if (detected_word <= 0) {
            continue;
        }

        const uint64_t detected_sample_index = s_total_sample_index + offset;
        if (s_wake_count != 0U &&
            detected_sample_index - s_last_wake_sample_index <
                MIC_WAKE_COOLDOWN_SAMPLES) {
            continue;
        }

        s_last_wake_sample_index = detected_sample_index;
        ++s_wake_count;
        const char *wake_word =
            s_wakenet->get_word_name(s_wakenet_data, detected_word);
        ESP_LOGI(
            TAG,
            "wake detected: word=%s index=%d count=%" PRIu32,
            wake_word != NULL ? wake_word : "Hi ESP",
            detected_word,
            s_wake_count);

        if (s_wake_armed) {
            s_wake_armed = false;
            reset_capture_session();
            s_streaming = true;
            ESP_LOGI(TAG, "armed capture started after Hi ESP");
        }
        if (ble_uart_is_connected() && ble_uart_is_subscribed()) {
            send_wake_event(detected_sample_index, (uint16_t)detected_word);
            send_status();
        }
    }
}

static void command_task(void *arg)
{
    (void)arg;
    mic_command_t command = {0};
    for (;;) {
        if (xQueueReceive(s_command_queue, &command, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        if (strncmp(command.text, "ARM PCM16", 9U) == 0) {
            s_encoding = MIC_ENCODING_PCM16;
            s_streaming = false;
            reset_capture_session();
            s_wake_armed = true;
            ESP_LOGI(TAG, "armed for Hi ESP: PCM16");
        } else if (strncmp(command.text, "ARM PCM8", 8U) == 0) {
            s_encoding = MIC_ENCODING_PCM8;
            s_streaming = false;
            reset_capture_session();
            s_wake_armed = true;
            ESP_LOGI(TAG, "armed for Hi ESP: PCM8");
        } else if (strncmp(command.text, "ARM ADPCM", 9U) == 0) {
            s_encoding = MIC_ENCODING_IMA_ADPCM;
            s_streaming = false;
            reset_capture_session();
            s_wake_armed = true;
            ESP_LOGI(TAG, "armed for Hi ESP: IMA ADPCM");
        } else if (strncmp(command.text, "START PCM16", 11U) == 0) {
            s_encoding = MIC_ENCODING_PCM16;
            reset_capture_session();
            s_wake_armed = false;
            s_streaming = true;
            ESP_LOGI(TAG, "capture started: PCM16");
        } else if (strncmp(command.text, "START PCM8", 10U) == 0) {
            s_encoding = MIC_ENCODING_PCM8;
            reset_capture_session();
            s_wake_armed = false;
            s_streaming = true;
            ESP_LOGI(TAG, "capture started: PCM8");
        } else if (strncmp(command.text, "START ADPCM", 11U) == 0) {
            s_encoding = MIC_ENCODING_IMA_ADPCM;
            reset_capture_session();
            s_wake_armed = false;
            s_streaming = true;
            ESP_LOGI(TAG, "capture started: IMA ADPCM");
        } else if (strncmp(command.text, "STOP", 4U) == 0) {
            s_streaming = false;
            s_wake_armed = false;
            if (s_tx_queue != NULL) {
                (void)xQueueReset(s_tx_queue);
            }
            ESP_LOGI(TAG, "capture stopped");
        } else if (strncmp(command.text, "SHIFT ", 6U) == 0) {
            unsigned shift = 0U;
            if (sscanf(command.text + 6, "%u", &shift) == 1 &&
                shift >= 10U && shift <= 20U) {
                s_pcm_shift = (uint8_t)shift;
                ESP_LOGI(TAG, "PCM right shift=%u", shift);
            }
        }
        send_status();
    }
}

static void tx_task(void *arg)
{
    (void)arg;
    mic_tx_item_t item = {0};
    for (;;) {
        if (xQueueReceive(s_tx_queue, &item, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        if (!s_streaming ||
            !ble_uart_is_connected() ||
            !ble_uart_is_subscribed()) {
            continue;
        }
        if (ble_uart_tx(item.data, item.length) != BLE_UART_OK) {
            ++s_tx_error_count;
        }
    }
}

static void audio_task(void *arg)
{
    (void)arg;
    int32_t raw[MIC_FRAME_SAMPLES] = {0};
    int16_t pcm[MIC_FRAME_SAMPLES] = {0};
    mic_tx_item_t item = {0};

    for (;;) {
        size_t bytes_read = 0U;
        const esp_err_t err = i2s_channel_read(
            s_rx_channel,
            raw,
            sizeof(raw),
            &bytes_read,
            200U);
        if (err != ESP_OK) {
            if (s_streaming || s_wake_armed) {
                ++s_i2s_error_count;
            }
            if (err != ESP_ERR_TIMEOUT) {
                ESP_LOGW(TAG, "I2S read failed: %s", esp_err_to_name(err));
            }
            continue;
        }

        const size_t sample_count = bytes_read / sizeof(raw[0]);
        bool clipped = false;
        const uint8_t shift = s_pcm_shift;
        for (size_t i = 0; i < sample_count; ++i) {
            pcm[i] = pcm16_from_i2s(raw[i], shift, &clipped);
        }
        process_wakenet(pcm, sample_count);
        s_total_sample_index += sample_count;

        const bool connected = ble_uart_is_connected();
        if (!connected) {
            s_conn_params_requested = false;
            s_conn_interval = 0U;
            s_was_connected = false;
            s_streaming = false;
            s_wake_armed = false;
            (void)xQueueReset(s_tx_queue);
            continue;
        }
        if (!s_was_connected) {
            s_was_connected = true;
            refresh_connection_parameters(true);
        } else if (!s_conn_params_requested) {
            refresh_connection_parameters(true);
        }
        if (!ble_uart_is_subscribed()) {
            s_streaming = false;
            s_wake_armed = false;
            continue;
        }
        if (!s_streaming) {
            continue;
        }

        if (clipped) {
            ++s_clipped_frame_count;
        }

        const uint8_t encoding = s_encoding;
        uint16_t payload_length = 0U;
        if (encoding == MIC_ENCODING_IMA_ADPCM) {
            payload_length = encode_ima_adpcm(
                pcm, sample_count, item.data + MIC_HEADER_SIZE);
        } else {
            const size_t bytes_per_sample =
                encoding == MIC_ENCODING_PCM8 ? sizeof(int8_t) : sizeof(int16_t);
            payload_length = (uint16_t)(sample_count * bytes_per_sample);
        }
        uint16_t flags = clipped ? MIC_FLAG_CLIPPED : 0U;
        if (s_i2s_error_count != 0U) {
            flags |= MIC_FLAG_I2S_ERROR;
        }
        if (s_tx_error_count != 0U) {
            flags |= MIC_FLAG_TX_ERROR;
        }

        const size_t payload_offset = begin_frame(
            item.data,
            MIC_FRAME_TYPE_AUDIO,
            encoding,
            flags,
            s_sequence,
            s_first_sample_index,
            (uint16_t)sample_count,
            payload_length);
        if (encoding == MIC_ENCODING_PCM8) {
            for (size_t i = 0; i < sample_count; ++i) {
                item.data[payload_offset + i] = (uint8_t)(int8_t)(pcm[i] >> 8);
            }
        } else if (encoding == MIC_ENCODING_PCM16) {
            for (size_t i = 0; i < sample_count; ++i) {
                write_le16(item.data + payload_offset + (i * 2U), (uint16_t)pcm[i]);
            }
        }

        const size_t crc_offset = payload_offset + payload_length;
        write_le16(
            item.data + crc_offset,
            crc16_ccitt_false(item.data, crc_offset));
        item.length = (uint16_t)(crc_offset + MIC_CRC_SIZE);
        ++s_sequence;
        s_first_sample_index += sample_count;
        if (xQueueSend(s_tx_queue, &item, 0U) != pdTRUE) {
            ++s_tx_error_count;
        }
        if ((s_sequence % 40U) == 0U) {
            send_status();
        }
    }
}

static void init_i2s(void)
{
    const i2s_chan_config_t channel_config =
        I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    ESP_ERROR_CHECK(i2s_new_channel(&channel_config, NULL, &s_rx_channel));

    i2s_std_config_t standard_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(MIC_SAMPLE_RATE_HZ),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
            I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = MIC_BCLK_GPIO,
            .ws = MIC_WS_GPIO,
            .dout = I2S_GPIO_UNUSED,
            .din = MIC_SD_GPIO,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };
    standard_config.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(s_rx_channel, &standard_config));
    ESP_ERROR_CHECK(i2s_channel_enable(s_rx_channel));
}

static bool init_wakenet(void)
{
    s_srmodels = esp_srmodel_init("model");
    if (s_srmodels == NULL) {
        ESP_LOGE(TAG, "failed to load speech models from model partition");
        return false;
    }

    char *model_name = esp_srmodel_filter(s_srmodels, ESP_WN_PREFIX, "hiesp");
    if (model_name == NULL) {
        ESP_LOGE(TAG, "wn9s_hiesp model not found");
        return false;
    }

    s_wakenet = esp_wn_handle_from_name(model_name);
    if (s_wakenet == NULL) {
        ESP_LOGE(TAG, "WakeNet interface not found for %s", model_name);
        return false;
    }
    s_wakenet_data = s_wakenet->create(model_name, DET_MODE_90);
    if (s_wakenet_data == NULL) {
        ESP_LOGE(TAG, "failed to create WakeNet model %s", model_name);
        return false;
    }

    const int chunk_samples = s_wakenet->get_samp_chunksize(s_wakenet_data);
    const int sample_rate = s_wakenet->get_samp_rate(s_wakenet_data);
    const int channel_count = s_wakenet->get_channel_num(s_wakenet_data);
    if (chunk_samples <= 0 ||
        sample_rate != (int)MIC_SAMPLE_RATE_HZ ||
        channel_count != 1) {
        ESP_LOGE(
            TAG,
            "unsupported WakeNet format: chunk=%d rate=%d channels=%d",
            chunk_samples,
            sample_rate,
            channel_count);
        return false;
    }

    s_wakenet_chunk_samples = (size_t)chunk_samples;
    s_wakenet_buffer =
        calloc(s_wakenet_chunk_samples, sizeof(s_wakenet_buffer[0]));
    if (s_wakenet_buffer == NULL) {
        ESP_LOGE(
            TAG,
            "failed to allocate %u WakeNet samples",
            (unsigned)s_wakenet_chunk_samples);
        return false;
    }

    ESP_LOGI(
        TAG,
        "WakeNet ready: model=%s word=%s chunk=%u rate=%d",
        model_name,
        s_wakenet->get_word_name(s_wakenet_data, 1),
        (unsigned)s_wakenet_chunk_samples,
        sample_rate);
    return true;
}

void app_main(void)
{
    ESP_ERROR_CHECK(nvs_flash_init());

    s_command_queue = xQueueCreate(8U, sizeof(mic_command_t));
    s_tx_queue = xQueueCreate(MIC_TX_QUEUE_DEPTH, sizeof(mic_tx_item_t));
    if (s_command_queue == NULL || s_tx_queue == NULL) {
        ESP_LOGE(TAG, "failed to allocate command or TX queue");
        return;
    }

    uint8_t mac[6] = {0};
    ESP_ERROR_CHECK(esp_read_mac(mac, ESP_MAC_BT));
    char device_name[24] = {0};
    (void)snprintf(
        device_name, sizeof(device_name), "CollarMic-%02X%02X", mac[4], mac[5]);

    const int install_result = ble_uart_install(&(ble_uart_config_t){
        .encrypted = false,
        .device_name = device_name,
        .ble_uart_on_rx = on_ble_rx,
    });
    if (install_result != BLE_UART_OK) {
        ESP_LOGE(TAG, "BLE UART install failed: %d", install_result);
        return;
    }
    if (ble_uart_open() != BLE_UART_OK) {
        ESP_LOGE(TAG, "BLE UART open failed");
        return;
    }

    init_i2s();
    if (!init_wakenet()) {
        ESP_LOGE(TAG, "Hi ESP initialization failed");
        return;
    }
    if (xTaskCreate(command_task, "mic_command", 3072U, NULL, 5U, NULL) != pdPASS ||
        xTaskCreate(tx_task, "mic_tx", 4096U, NULL, 5U, NULL) != pdPASS ||
        xTaskCreate(audio_task, "mic_audio", 6144U, NULL, 6U, NULL) != pdPASS) {
        ESP_LOGE(TAG, "failed to create capture tasks");
        return;
    }

    ESP_LOGI(
        TAG,
        "ready device=%s rate=%uHz BCLK=GPIO%d WS=GPIO%d SD=GPIO%d L/R=GND shift=%u",
        device_name,
        MIC_SAMPLE_RATE_HZ,
        MIC_BCLK_GPIO,
        MIC_WS_GPIO,
        MIC_SD_GPIO,
        MIC_INITIAL_SHIFT);
}
