#include <limits.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "ble_uart.h"
#include "driver/i2s_std.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#define MIC_SAMPLE_RATE_HZ 16000U
#define MIC_FRAME_SAMPLES 320U
#define MIC_BCLK_GPIO GPIO_NUM_4
#define MIC_WS_GPIO GPIO_NUM_5
#define MIC_SD_GPIO GPIO_NUM_20
#define MIC_INITIAL_SHIFT 14U

#define MIC_FRAME_TYPE_AUDIO 1U
#define MIC_FRAME_TYPE_STATUS 2U
#define MIC_ENCODING_PCM16 1U
#define MIC_ENCODING_PCM8 2U

#define MIC_FLAG_CLIPPED (1U << 0)
#define MIC_FLAG_I2S_ERROR (1U << 1)
#define MIC_FLAG_TX_ERROR (1U << 2)

#define MIC_HEADER_SIZE 29U
#define MIC_CRC_SIZE 2U
#define MIC_PCM16_PAYLOAD_BYTES (MIC_FRAME_SAMPLES * sizeof(int16_t))
#define MIC_MAX_FRAME_BYTES (MIC_HEADER_SIZE + MIC_PCM16_PAYLOAD_BYTES + MIC_CRC_SIZE)

static const char *TAG = "mic_capture";
static const uint8_t MIC_MAGIC[4] = {'M', 'I', 'C', '1'};

typedef struct {
    char text[32];
} mic_command_t;

static i2s_chan_handle_t s_rx_channel;
static QueueHandle_t s_command_queue;
static volatile bool s_streaming;
static volatile uint8_t s_encoding = MIC_ENCODING_PCM16;
static volatile uint8_t s_pcm_shift = MIC_INITIAL_SHIFT;
static uint32_t s_sequence;
static uint64_t s_first_sample_index;
static uint32_t s_i2s_error_count;
static uint32_t s_tx_error_count;
static uint32_t s_clipped_frame_count;

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
    write_le32(frame + payload_offset + 12U, 0U);
    write_le16(frame + payload_offset + 16U, s_pcm_shift);
    frame[payload_offset + 18U] = s_encoding;
    frame[payload_offset + 19U] = s_streaming ? 1U : 0U;
    const size_t crc_offset = payload_offset + 20U;
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

static void command_task(void *arg)
{
    (void)arg;
    mic_command_t command = {0};
    for (;;) {
        if (xQueueReceive(s_command_queue, &command, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        if (strncmp(command.text, "START PCM16", 11U) == 0) {
            s_encoding = MIC_ENCODING_PCM16;
            s_sequence = 0U;
            s_first_sample_index = 0U;
            s_streaming = true;
            ESP_LOGI(TAG, "capture started: PCM16");
        } else if (strncmp(command.text, "START PCM8", 10U) == 0) {
            s_encoding = MIC_ENCODING_PCM8;
            s_sequence = 0U;
            s_first_sample_index = 0U;
            s_streaming = true;
            ESP_LOGI(TAG, "capture started: PCM8");
        } else if (strncmp(command.text, "STOP", 4U) == 0) {
            s_streaming = false;
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

static void audio_task(void *arg)
{
    (void)arg;
    int32_t raw[MIC_FRAME_SAMPLES] = {0};
    int16_t pcm[MIC_FRAME_SAMPLES] = {0};
    uint8_t frame[MIC_MAX_FRAME_BYTES] = {0};

    for (;;) {
        size_t bytes_read = 0U;
        const esp_err_t err = i2s_channel_read(
            s_rx_channel,
            raw,
            sizeof(raw),
            &bytes_read,
            pdMS_TO_TICKS(200U));
        if (err != ESP_OK) {
            ++s_i2s_error_count;
            ESP_LOGW(TAG, "I2S read failed: %s", esp_err_to_name(err));
            continue;
        }
        if (!s_streaming || !ble_uart_is_connected() || !ble_uart_is_subscribed()) {
            continue;
        }

        const size_t sample_count = bytes_read / sizeof(raw[0]);
        bool clipped = false;
        const uint8_t shift = s_pcm_shift;
        for (size_t i = 0; i < sample_count; ++i) {
            pcm[i] = pcm16_from_i2s(raw[i], shift, &clipped);
        }
        if (clipped) {
            ++s_clipped_frame_count;
        }

        const uint8_t encoding = s_encoding;
        const size_t bytes_per_sample =
            encoding == MIC_ENCODING_PCM8 ? sizeof(int8_t) : sizeof(int16_t);
        const uint16_t payload_length =
            (uint16_t)(sample_count * bytes_per_sample);
        uint16_t flags = clipped ? MIC_FLAG_CLIPPED : 0U;
        if (s_i2s_error_count != 0U) {
            flags |= MIC_FLAG_I2S_ERROR;
        }
        if (s_tx_error_count != 0U) {
            flags |= MIC_FLAG_TX_ERROR;
        }

        const size_t payload_offset = begin_frame(
            frame,
            MIC_FRAME_TYPE_AUDIO,
            encoding,
            flags,
            s_sequence,
            s_first_sample_index,
            (uint16_t)sample_count,
            payload_length);
        if (encoding == MIC_ENCODING_PCM8) {
            for (size_t i = 0; i < sample_count; ++i) {
                frame[payload_offset + i] = (uint8_t)(int8_t)(pcm[i] >> 8);
            }
        } else {
            for (size_t i = 0; i < sample_count; ++i) {
                write_le16(frame + payload_offset + (i * 2U), (uint16_t)pcm[i]);
            }
        }

        const size_t crc_offset = payload_offset + payload_length;
        write_le16(frame + crc_offset, crc16_ccitt_false(frame, crc_offset));
        const int tx_result = ble_uart_tx(frame, crc_offset + MIC_CRC_SIZE);
        ++s_sequence;
        s_first_sample_index += sample_count;
        if (tx_result != BLE_UART_OK) {
            ++s_tx_error_count;
        }
        if ((s_sequence % 50U) == 0U) {
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

void app_main(void)
{
    ESP_ERROR_CHECK(nvs_flash_init());

    s_command_queue = xQueueCreate(8U, sizeof(mic_command_t));
    if (s_command_queue == NULL) {
        ESP_LOGE(TAG, "failed to allocate command queue");
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
    if (xTaskCreate(command_task, "mic_command", 3072U, NULL, 5U, NULL) != pdPASS ||
        xTaskCreate(audio_task, "mic_audio", 4096U, NULL, 6U, NULL) != pdPASS) {
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
