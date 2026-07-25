#include "mic_runtime.h"

#include <inttypes.h>
#include <limits.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "board_config.h"
#include "driver/i2s_std.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_wn_iface.h"
#include "esp_wn_models.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "model_path.h"
#include "transport.h"

#define MIC_FRAME_SAMPLES 400U
#define MIC_BLOCK_COUNT 4U
#define MIC_WAKE_COOLDOWN_SAMPLES (BOARD_INMP441_SAMPLE_RATE_HZ * 2U)
#define MIC_MAX_STREAM_SAMPLES (BOARD_INMP441_SAMPLE_RATE_HZ * 30U)

#define MIC_FRAME_TYPE_AUDIO 1U
#define MIC_FRAME_TYPE_STATUS 2U
#define MIC_FRAME_TYPE_WAKE 3U
#define MIC_ENCODING_IMA_ADPCM 3U

#define MIC_FLAG_CLIPPED (1U << 0)
#define MIC_FLAG_I2S_ERROR (1U << 1)
#define MIC_FLAG_TX_ERROR (1U << 2)

#define MIC_HEADER_SIZE 29U
#define MIC_CRC_SIZE 2U
#define MIC_ADPCM_PAYLOAD_BYTES (4U + (MIC_FRAME_SAMPLES / 2U))
#define MIC_AUDIO_FRAME_BYTES (MIC_HEADER_SIZE + MIC_ADPCM_PAYLOAD_BYTES + MIC_CRC_SIZE)

typedef struct {
    uint64_t first_sample_index;
    bool clipped;
    int16_t pcm[MIC_FRAME_SAMPLES];
} mic_pcm_block_t;

static const char *TAG = "mic_runtime";
static const uint8_t MIC_MAGIC[4] = {'M', 'I', 'C', '1'};

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

static i2s_chan_handle_t s_rx_channel = NULL;
static QueueHandle_t s_free_blocks = NULL;
static QueueHandle_t s_ready_blocks = NULL;
static TaskHandle_t s_capture_task = NULL;
static TaskHandle_t s_worker_task = NULL;
static mic_pcm_block_t s_blocks[MIC_BLOCK_COUNT];

static srmodel_list_t *s_srmodels = NULL;
static const esp_wn_iface_t *s_wakenet = NULL;
static model_iface_data_t *s_wakenet_data = NULL;
static int16_t *s_wakenet_buffer = NULL;
static size_t s_wakenet_chunk_samples = 0U;
static size_t s_wakenet_buffered_samples = 0U;

static volatile bool s_enabled = true;
static volatile bool s_stop_requested = false;
static volatile bool s_status_requested = false;
static volatile uint8_t s_pcm_shift = BOARD_INMP441_PCM_SHIFT;
static volatile mic_runtime_state_t s_state = MIC_RUNTIME_DISABLED;

static uint64_t s_total_sample_index = 0U;
static uint64_t s_last_wake_sample_index = 0U;
static uint64_t s_stream_start_sample_index = 0U;
static uint32_t s_audio_sequence = 0U;
static int s_adpcm_index = 0;

static uint32_t s_wake_count = 0U;
static uint32_t s_i2s_error_count = 0U;
static uint32_t s_tx_error_count = 0U;
static uint32_t s_clipped_frame_count = 0U;
static uint32_t s_control_error_count = 0U;

static void increment(uint32_t *value)
{
    (void)__atomic_fetch_add(value, 1U, __ATOMIC_RELAXED);
}

static uint32_t snapshot(const uint32_t *value)
{
    return __atomic_load_n(value, __ATOMIC_RELAXED);
}

static void write_le16(uint8_t *out, uint16_t value)
{
    out[0] = (uint8_t)value;
    out[1] = (uint8_t)(value >> 8);
}

static void write_le32(uint8_t *out, uint32_t value)
{
    for (size_t i = 0U; i < 4U; ++i) {
        out[i] = (uint8_t)(value >> (8U * i));
    }
}

static void write_le64(uint8_t *out, uint64_t value)
{
    for (size_t i = 0U; i < 8U; ++i) {
        out[i] = (uint8_t)(value >> (8U * i));
    }
}

static uint16_t crc16_ccitt_false(const uint8_t *data, size_t length)
{
    uint16_t crc = 0xFFFFU;
    for (size_t i = 0U; i < length; ++i) {
        crc ^= (uint16_t)data[i] << 8;
        for (unsigned bit = 0U; bit < 8U; ++bit) {
            crc = (crc & 0x8000U) != 0U ?
                  (uint16_t)((crc << 1) ^ 0x1021U) :
                  (uint16_t)(crc << 1);
        }
    }
    return crc;
}

static size_t begin_frame(uint8_t *frame,
                          uint8_t frame_type,
                          uint16_t flags,
                          uint32_t sequence,
                          uint64_t first_sample_index,
                          uint16_t sample_count,
                          uint16_t payload_length)
{
    memcpy(frame, MIC_MAGIC, sizeof(MIC_MAGIC));
    frame[4] = 1U;
    frame[5] = frame_type;
    frame[6] = MIC_ENCODING_IMA_ADPCM;
    write_le16(frame + 7U, flags);
    write_le32(frame + 9U, sequence);
    write_le32(frame + 13U, BOARD_INMP441_SAMPLE_RATE_HZ);
    write_le64(frame + 17U, first_sample_index);
    write_le16(frame + 25U, sample_count);
    write_le16(frame + 27U, payload_length);
    return MIC_HEADER_SIZE;
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

static uint16_t encode_ima_adpcm(const int16_t *pcm, uint8_t *payload)
{
    int predictor = pcm[0];
    int index = s_adpcm_index;
    write_le16(payload, (uint16_t)predictor);
    payload[2] = (uint8_t)index;
    payload[3] = 0U;
    memset(payload + 4U, 0, MIC_FRAME_SAMPLES / 2U);

    for (size_t i = 1U; i < MIC_FRAME_SAMPLES; ++i) {
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
    return MIC_ADPCM_PAYLOAD_BYTES;
}

static bool link_ready(v0_transport_status_t *status)
{
    v0_transport_get_status(status);
    return status->connected && status->subscribed;
}

static bool send_frame(const uint8_t *frame, size_t length)
{
    if (v0_transport_enqueue_low_priority(frame, length)) {
        return true;
    }
    increment(&s_tx_error_count);
    return false;
}

static void send_status(uint64_t sample_index, bool armed)
{
    v0_transport_status_t transport = {0};
    if (!link_ready(&transport)) {
        return;
    }

    uint8_t frame[MIC_HEADER_SIZE + 20U + MIC_CRC_SIZE] = {0};
    const size_t payload = begin_frame(frame,
                                       MIC_FRAME_TYPE_STATUS,
                                       0U,
                                       s_audio_sequence,
                                       sample_index,
                                       0U,
                                       20U);
    write_le32(frame + payload, snapshot(&s_i2s_error_count));
    write_le32(frame + payload + 4U, snapshot(&s_tx_error_count));
    write_le32(frame + payload + 8U, snapshot(&s_clipped_frame_count));
    write_le32(frame + payload + 12U, transport.connection_interval_units);
    write_le16(frame + payload + 16U, s_pcm_shift);
    frame[payload + 18U] = MIC_ENCODING_IMA_ADPCM;
    frame[payload + 19U] = s_state == MIC_RUNTIME_STREAMING ? 1U : (armed ? 2U : 0U);
    const size_t crc_offset = payload + 20U;
    write_le16(frame + crc_offset, crc16_ccitt_false(frame, crc_offset));
    (void)send_frame(frame, crc_offset + MIC_CRC_SIZE);
}

static void send_wake(uint64_t detected_sample_index, uint16_t word_index)
{
    uint8_t frame[MIC_HEADER_SIZE + 8U + MIC_CRC_SIZE] = {0};
    const size_t payload = begin_frame(frame,
                                       MIC_FRAME_TYPE_WAKE,
                                       0U,
                                       s_wake_count,
                                       detected_sample_index,
                                       0U,
                                       8U);
    write_le32(frame + payload, s_wake_count);
    write_le16(frame + payload + 4U, word_index);
    write_le16(frame + payload + 6U, 0U);
    const size_t crc_offset = payload + 8U;
    write_le16(frame + crc_offset, crc16_ccitt_false(frame, crc_offset));
    (void)send_frame(frame, crc_offset + MIC_CRC_SIZE);
}

static void reset_stream(uint64_t first_sample_index)
{
    s_audio_sequence = 0U;
    s_adpcm_index = 0;
    s_stream_start_sample_index = first_sample_index;
}

bool mic_runtime_protocol_self_test(void)
{
    static const uint8_t expected[] = {
        0x4d, 0x49, 0x43, 0x31, 0x01, 0x02, 0x03, 0x00, 0x00, 0x07,
        0x00, 0x00, 0x00, 0x80, 0x3e, 0x00, 0x00, 0x40, 0xe2, 0x01,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x14, 0x00, 0x01,
        0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x03, 0x00, 0x00,
        0x00, 0x08, 0x00, 0x00, 0x00, 0x10, 0x00, 0x03, 0x02, 0x3c,
        0x70,
    };
    uint8_t frame[sizeof(expected)] = {0};
    const size_t payload = begin_frame(frame,
                                       MIC_FRAME_TYPE_STATUS,
                                       0U,
                                       7U,
                                       123456U,
                                       0U,
                                       20U);
    write_le32(frame + payload, 1U);
    write_le32(frame + payload + 4U, 2U);
    write_le32(frame + payload + 8U, 3U);
    write_le32(frame + payload + 12U, 8U);
    write_le16(frame + payload + 16U, 16U);
    frame[payload + 18U] = MIC_ENCODING_IMA_ADPCM;
    frame[payload + 19U] = 2U;
    const size_t crc_offset = payload + 20U;
    write_le16(frame + crc_offset, crc16_ccitt_false(frame, crc_offset));
    return memcmp(frame, expected, sizeof(expected)) == 0;
}

static int detect_wake(const mic_pcm_block_t *block)
{
    size_t offset = 0U;
    while (offset < MIC_FRAME_SAMPLES) {
        const size_t remaining = MIC_FRAME_SAMPLES - offset;
        const size_t capacity = s_wakenet_chunk_samples - s_wakenet_buffered_samples;
        const size_t copy_count = remaining < capacity ? remaining : capacity;
        memcpy(s_wakenet_buffer + s_wakenet_buffered_samples,
               block->pcm + offset,
               copy_count * sizeof(block->pcm[0]));
        offset += copy_count;
        s_wakenet_buffered_samples += copy_count;
        if (s_wakenet_buffered_samples != s_wakenet_chunk_samples) {
            continue;
        }

        const int word = (int)s_wakenet->detect(s_wakenet_data, s_wakenet_buffer);
        s_wakenet_buffered_samples = 0U;
        if (word <= 0) {
            continue;
        }
        const uint64_t detected_index = block->first_sample_index + offset;
        if (s_wake_count != 0U &&
            detected_index - s_last_wake_sample_index < MIC_WAKE_COOLDOWN_SAMPLES) {
            continue;
        }
        s_last_wake_sample_index = detected_index;
        return word;
    }
    return 0;
}

static void send_audio(const mic_pcm_block_t *block)
{
    uint8_t frame[MIC_AUDIO_FRAME_BYTES] = {0};
    uint16_t flags = block->clipped ? MIC_FLAG_CLIPPED : 0U;
    if (snapshot(&s_i2s_error_count) != 0U) {
        flags |= MIC_FLAG_I2S_ERROR;
    }
    if (snapshot(&s_tx_error_count) != 0U) {
        flags |= MIC_FLAG_TX_ERROR;
    }
    const uint32_t sequence = s_audio_sequence++;
    const size_t payload = begin_frame(frame,
                                       MIC_FRAME_TYPE_AUDIO,
                                       flags,
                                       sequence,
                                       block->first_sample_index,
                                       MIC_FRAME_SAMPLES,
                                       MIC_ADPCM_PAYLOAD_BYTES);
    (void)encode_ima_adpcm(block->pcm, frame + payload);
    const size_t crc_offset = payload + MIC_ADPCM_PAYLOAD_BYTES;
    write_le16(frame + crc_offset, crc16_ccitt_false(frame, crc_offset));
    (void)send_frame(frame, crc_offset + MIC_CRC_SIZE);
}

static void worker_task(void *arg)
{
    (void)arg;
    mic_pcm_block_t *block = NULL;
    bool was_ready = false;

    for (;;) {
        if (xQueueReceive(s_ready_blocks, &block, portMAX_DELAY) != pdTRUE) {
            continue;
        }

        v0_transport_status_t transport = {0};
        const bool ready = link_ready(&transport);
        if (!ready) {
            s_state = s_enabled ? MIC_RUNTIME_DISCONNECTED : MIC_RUNTIME_DISABLED;
            s_stop_requested = false;
        } else if (!s_enabled) {
            s_state = MIC_RUNTIME_DISABLED;
        } else if (s_state != MIC_RUNTIME_STREAMING) {
            s_state = MIC_RUNTIME_ARMED;
        }

        if (ready != was_ready || s_status_requested) {
            send_status(block->first_sample_index, s_state == MIC_RUNTIME_ARMED);
            s_status_requested = false;
            was_ready = ready;
        }

        if (s_stop_requested) {
            if (s_state == MIC_RUNTIME_STREAMING) {
                s_state = s_enabled && ready ? MIC_RUNTIME_ARMED : MIC_RUNTIME_DISCONNECTED;
                send_status(block->first_sample_index, s_state == MIC_RUNTIME_ARMED);
            }
            s_stop_requested = false;
        }

        if (s_state == MIC_RUNTIME_ARMED) {
            const int word = detect_wake(block);
            if (word > 0) {
                ++s_wake_count;
                s_state = MIC_RUNTIME_STREAMING;
                reset_stream(block->first_sample_index);
                ESP_LOGI(TAG,
                         "Hi ESP detected word=%d wake_count=%" PRIu32,
                         word,
                         s_wake_count);
                send_wake(block->first_sample_index, (uint16_t)word);
                send_status(block->first_sample_index, false);
            }
        }

        if (s_state == MIC_RUNTIME_STREAMING) {
            if (!ready ||
                block->first_sample_index - s_stream_start_sample_index >=
                    MIC_MAX_STREAM_SAMPLES) {
                s_state = s_enabled && ready ? MIC_RUNTIME_ARMED : MIC_RUNTIME_DISCONNECTED;
                send_status(block->first_sample_index, s_state == MIC_RUNTIME_ARMED);
            } else {
                if (block->clipped) {
                    increment(&s_clipped_frame_count);
                }
                send_audio(block);
                if ((s_audio_sequence % 40U) == 0U) {
                    send_status(block->first_sample_index, false);
                }
            }
        }

        (void)xQueueSend(s_free_blocks, &block, 0U);
    }
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

static void capture_task(void *arg)
{
    (void)arg;
    int32_t raw[MIC_FRAME_SAMPLES] = {0};

    for (;;) {
        size_t bytes_read = 0U;
        const esp_err_t err = i2s_channel_read(s_rx_channel,
                                               raw,
                                               sizeof(raw),
                                               &bytes_read,
                                               pdMS_TO_TICKS(200U));
        if (err != ESP_OK || bytes_read != sizeof(raw)) {
            increment(&s_i2s_error_count);
            continue;
        }

        mic_pcm_block_t *block = NULL;
        if (xQueueReceive(s_free_blocks, &block, 0U) != pdTRUE) {
            increment(&s_i2s_error_count);
            s_total_sample_index += MIC_FRAME_SAMPLES;
            continue;
        }

        block->first_sample_index = s_total_sample_index;
        block->clipped = false;
        const uint8_t shift = s_pcm_shift;
        for (size_t i = 0U; i < MIC_FRAME_SAMPLES; ++i) {
            block->pcm[i] = pcm16_from_i2s(raw[i], shift, &block->clipped);
        }
        s_total_sample_index += MIC_FRAME_SAMPLES;

        if (xQueueSend(s_ready_blocks, &block, 0U) != pdTRUE) {
            increment(&s_i2s_error_count);
            (void)xQueueSend(s_free_blocks, &block, 0U);
        }
    }
}

static esp_err_t init_i2s(void)
{
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
        (void)i2s_del_channel(s_rx_channel);
        s_rx_channel = NULL;
        return err;
    }
    err = i2s_channel_enable(s_rx_channel);
    if (err != ESP_OK) {
        (void)i2s_del_channel(s_rx_channel);
        s_rx_channel = NULL;
    }
    return err;
}

static void cleanup_startup_resources(void)
{
    if (s_capture_task != NULL) {
        vTaskDelete(s_capture_task);
        s_capture_task = NULL;
    }
    if (s_worker_task != NULL) {
        vTaskDelete(s_worker_task);
        s_worker_task = NULL;
    }
    if (s_rx_channel != NULL) {
        (void)i2s_channel_disable(s_rx_channel);
        (void)i2s_del_channel(s_rx_channel);
        s_rx_channel = NULL;
    }
    free(s_wakenet_buffer);
    s_wakenet_buffer = NULL;
    s_wakenet_chunk_samples = 0U;
    s_wakenet_buffered_samples = 0U;
    if (s_wakenet != NULL && s_wakenet_data != NULL) {
        s_wakenet->destroy(s_wakenet_data);
    }
    s_wakenet_data = NULL;
    s_wakenet = NULL;
    if (s_srmodels != NULL) {
        esp_srmodel_deinit(s_srmodels);
        s_srmodels = NULL;
    }
    if (s_free_blocks != NULL) {
        vQueueDelete(s_free_blocks);
        s_free_blocks = NULL;
    }
    if (s_ready_blocks != NULL) {
        vQueueDelete(s_ready_blocks);
        s_ready_blocks = NULL;
    }
}

static esp_err_t init_wakenet(void)
{
    s_srmodels = esp_srmodel_init("model");
    if (s_srmodels == NULL) {
        return ESP_ERR_NOT_FOUND;
    }
    char *model_name = esp_srmodel_filter(s_srmodels, ESP_WN_PREFIX, "hiesp");
    if (model_name == NULL) {
        return ESP_ERR_NOT_FOUND;
    }
    s_wakenet = esp_wn_handle_from_name(model_name);
    if (s_wakenet == NULL) {
        return ESP_ERR_NOT_FOUND;
    }
    s_wakenet_data = s_wakenet->create(model_name, DET_MODE_90);
    if (s_wakenet_data == NULL) {
        return ESP_ERR_NO_MEM;
    }

    const int chunk_samples = s_wakenet->get_samp_chunksize(s_wakenet_data);
    const int sample_rate = s_wakenet->get_samp_rate(s_wakenet_data);
    const int channels = s_wakenet->get_channel_num(s_wakenet_data);
    if (chunk_samples <= 0 ||
        sample_rate != (int)BOARD_INMP441_SAMPLE_RATE_HZ ||
        channels != 1) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    s_wakenet_chunk_samples = (size_t)chunk_samples;
    s_wakenet_buffer = heap_caps_calloc(
        s_wakenet_chunk_samples, sizeof(int16_t), MALLOC_CAP_8BIT);
    if (s_wakenet_buffer == NULL) {
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG,
             "WakeNet ready model=%s word=%s chunk=%u rate=%d",
             model_name,
             s_wakenet->get_word_name(s_wakenet_data, 1),
             (unsigned)s_wakenet_chunk_samples,
             sample_rate);
    return ESP_OK;
}

esp_err_t mic_runtime_start(void)
{
    if (s_capture_task != NULL || s_worker_task != NULL) {
        return ESP_OK;
    }

    s_free_blocks = xQueueCreate(MIC_BLOCK_COUNT, sizeof(mic_pcm_block_t *));
    s_ready_blocks = xQueueCreate(MIC_BLOCK_COUNT, sizeof(mic_pcm_block_t *));
    if (s_free_blocks == NULL || s_ready_blocks == NULL) {
        s_state = MIC_RUNTIME_ERROR;
        cleanup_startup_resources();
        return ESP_ERR_NO_MEM;
    }
    for (size_t i = 0U; i < MIC_BLOCK_COUNT; ++i) {
        mic_pcm_block_t *block = &s_blocks[i];
        (void)xQueueSend(s_free_blocks, &block, 0U);
    }

    esp_err_t err = init_wakenet();
    if (err != ESP_OK) {
        s_state = MIC_RUNTIME_ERROR;
        ESP_LOGE(TAG, "WakeNet init failed: %s", esp_err_to_name(err));
        cleanup_startup_resources();
        return err;
    }
    err = init_i2s();
    if (err != ESP_OK) {
        s_state = MIC_RUNTIME_ERROR;
        ESP_LOGE(TAG, "INMP441 init failed: %s", esp_err_to_name(err));
        cleanup_startup_resources();
        return err;
    }

    if (xTaskCreate(worker_task,
                    "mic_worker",
                    BOARD_MIC_WORKER_TASK_STACK_BYTES,
                    NULL,
                    BOARD_MIC_WORKER_TASK_PRIORITY,
                    &s_worker_task) != pdPASS ||
        xTaskCreate(capture_task,
                    "mic_capture",
                    BOARD_VOICE_AUDIO_TASK_STACK_BYTES,
                    NULL,
                    BOARD_VOICE_AUDIO_TASK_PRIORITY,
                    &s_capture_task) != pdPASS) {
        s_state = MIC_RUNTIME_ERROR;
        cleanup_startup_resources();
        return ESP_ERR_NO_MEM;
    }

    s_state = MIC_RUNTIME_DISCONNECTED;
    ESP_LOGI(TAG,
             "unified microphone ready rate=%uHz frame=%u samples free_heap=%u",
             BOARD_INMP441_SAMPLE_RATE_HZ,
             MIC_FRAME_SAMPLES,
             (unsigned)esp_get_free_heap_size());
    return ESP_OK;
}

static bool command_equals(const uint8_t *data, size_t length, const char *text)
{
    const size_t text_length = strlen(text);
    while (length > 0U && (data[length - 1U] == '\r' || data[length - 1U] == '\n')) {
        --length;
    }
    return length == text_length && memcmp(data, text, text_length) == 0;
}

bool mic_runtime_handle_command(const uint8_t *data, size_t length)
{
    if (data == NULL || length < 4U || memcmp(data, "MIC ", 4U) != 0) {
        return false;
    }
    if (command_equals(data, length, "MIC ARM")) {
        s_enabled = true;
        s_stop_requested = true;
    } else if (command_equals(data, length, "MIC DISARM")) {
        s_enabled = false;
        s_stop_requested = true;
    } else if (command_equals(data, length, "MIC STOP")) {
        s_stop_requested = true;
    } else if (length >= 10U && memcmp(data, "MIC SHIFT ", 10U) == 0) {
        char value[8] = {0};
        size_t value_length = length - 10U;
        while (value_length > 0U &&
               (data[10U + value_length - 1U] == '\r' ||
                data[10U + value_length - 1U] == '\n')) {
            --value_length;
        }
        if (value_length >= sizeof(value)) {
            increment(&s_control_error_count);
        } else {
            memcpy(value, data + 10U, value_length);
            unsigned shift = 0U;
            bool valid = value_length > 0U;
            for (size_t i = 0U; i < value_length; ++i) {
                if (value[i] < '0' || value[i] > '9') {
                    valid = false;
                    break;
                }
                shift = shift * 10U + (unsigned)(value[i] - '0');
            }
            if (valid && shift >= 10U && shift <= 20U) {
                s_pcm_shift = (uint8_t)shift;
            } else {
                increment(&s_control_error_count);
            }
        }
    } else {
        increment(&s_control_error_count);
    }
    s_status_requested = true;
    return true;
}

void mic_runtime_record_control_error(void)
{
    increment(&s_control_error_count);
    s_status_requested = true;
}

void mic_runtime_get_status(mic_runtime_status_t *out_status)
{
    if (out_status == NULL) {
        return;
    }
    out_status->state = s_state;
    out_status->wake_count = snapshot(&s_wake_count);
    out_status->i2s_error_count = snapshot(&s_i2s_error_count);
    out_status->tx_error_count = snapshot(&s_tx_error_count);
    out_status->clipped_frame_count = snapshot(&s_clipped_frame_count);
    out_status->control_error_count = snapshot(&s_control_error_count);
    out_status->pcm_shift = s_pcm_shift;
}
