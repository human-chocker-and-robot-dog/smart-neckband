#include "voice_link.h"

#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "packet_task.h"
#include "transport.h"

#define VOICE_PENDING_TEXT_CAPACITY 4U
#define VOICE_RETRY_INTERVAL_US 2000000LL
#define VOICE_STATUS_INTERVAL_US 1000000LL

static const char *TAG = "v0_voice_link";

typedef struct {
    bool active;
    bool sent_once;
    uint64_t utterance_id;
    size_t text_length;
    uint8_t text[PROTOCOL_V0_VOICE_TEXT_MAX_BYTES];
    uint8_t next_chunk;
    int64_t last_cycle_us;
} pending_voice_text_t;

static pending_voice_text_t s_pending[VOICE_PENDING_TEXT_CAPACITY] = {0};
static v0_voice_link_status_t s_status = {
    .state = PROTOCOL_V0_VOICE_STATE_DISABLED,
};
static TaskHandle_t s_task_handle = NULL;
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static uint8_t s_rx_buffer[PROTOCOL_V0_VOICE_TEXT_ACK_PACKET_SIZE] = {0};
static size_t s_rx_length = 0U;

static uint8_t pending_count_locked(void)
{
    uint8_t count = 0U;
    for (size_t i = 0; i < VOICE_PENDING_TEXT_CAPACITY; ++i) {
        if (s_pending[i].active) {
            ++count;
        }
    }
    return count;
}

static void acknowledge_utterance(uint64_t utterance_id)
{
    portENTER_CRITICAL(&s_mux);
    for (size_t i = 0; i < VOICE_PENDING_TEXT_CAPACITY; ++i) {
        if (s_pending[i].active && s_pending[i].utterance_id == utterance_id) {
            memset(&s_pending[i], 0, sizeof(s_pending[i]));
            break;
        }
    }
    s_status.pending_text_count = pending_count_locked();
    portEXIT_CRITICAL(&s_mux);
}

static void consume_control_byte(uint8_t byte)
{
    if (s_rx_length == 0U) {
        if (byte == (uint8_t)(PROTOCOL_V0_MAGIC & 0xffU)) {
            s_rx_buffer[s_rx_length++] = byte;
        }
        return;
    }
    if (s_rx_length == 1U && byte != (uint8_t)(PROTOCOL_V0_MAGIC >> 8U)) {
        s_rx_length = byte == (uint8_t)(PROTOCOL_V0_MAGIC & 0xffU) ? 1U : 0U;
        s_rx_buffer[0] = byte;
        return;
    }

    s_rx_buffer[s_rx_length++] = byte;
    if (s_rx_length < sizeof(s_rx_buffer)) {
        return;
    }

    uint64_t utterance_id = 0U;
    if (protocol_v0_decode_voice_text_ack_packet(
            s_rx_buffer, sizeof(s_rx_buffer), &utterance_id)) {
        acknowledge_utterance(utterance_id);
    }
    s_rx_length = 0U;
}

static void on_transport_rx(const uint8_t *data, size_t length)
{
    if (data == NULL) {
        return;
    }
    for (size_t i = 0; i < length; ++i) {
        consume_control_byte(data[i]);
    }
}

static bool copy_due_text(int64_t now_us,
                          size_t *out_slot,
                          pending_voice_text_t *out_text)
{
    bool found = false;
    portENTER_CRITICAL(&s_mux);
    for (size_t i = 0; i < VOICE_PENDING_TEXT_CAPACITY; ++i) {
        const pending_voice_text_t *candidate = &s_pending[i];
        if (!candidate->active) {
            continue;
        }
        const bool mid_cycle = candidate->next_chunk > 0U;
        const bool retry_due =
            !candidate->sent_once ||
            now_us - candidate->last_cycle_us >= VOICE_RETRY_INTERVAL_US;
        if (!mid_cycle && !retry_due) {
            continue;
        }
        *out_slot = i;
        *out_text = *candidate;
        found = true;
        break;
    }
    portEXIT_CRITICAL(&s_mux);
    return found;
}

static void advance_sent_chunk(size_t slot,
                               uint64_t utterance_id,
                               uint8_t chunk_count,
                               int64_t now_us)
{
    portENTER_CRITICAL(&s_mux);
    pending_voice_text_t *pending = &s_pending[slot];
    if (pending->active && pending->utterance_id == utterance_id) {
        ++pending->next_chunk;
        if (pending->next_chunk >= chunk_count) {
            pending->next_chunk = 0U;
            pending->last_cycle_us = now_us;
            pending->sent_once = true;
        }
    }
    portEXIT_CRITICAL(&s_mux);
}

static void try_send_text_chunk(int64_t now_us)
{
    pending_voice_text_t pending = {0};
    size_t slot = 0U;
    if (!copy_due_text(now_us, &slot, &pending)) {
        return;
    }

    const uint8_t chunk_count = (uint8_t)(
        (pending.text_length + PROTOCOL_V0_VOICE_TEXT_DATA_SIZE - 1U) /
        PROTOCOL_V0_VOICE_TEXT_DATA_SIZE);
    const size_t offset =
        (size_t)pending.next_chunk * PROTOCOL_V0_VOICE_TEXT_DATA_SIZE;
    const size_t remaining = pending.text_length - offset;
    const uint8_t chunk_length = (uint8_t)(
        remaining < PROTOCOL_V0_VOICE_TEXT_DATA_SIZE ?
        remaining : PROTOCOL_V0_VOICE_TEXT_DATA_SIZE);

    protocol_v0_voice_text_chunk_payload_t chunk = {
        .utterance_id = pending.utterance_id,
        .chunk_index = pending.next_chunk,
        .chunk_count = chunk_count,
        .text_length = chunk_length,
        .flags = PROTOCOL_V0_VOICE_TEXT_FLAG_FINAL |
                 (pending.sent_once ? PROTOCOL_V0_VOICE_TEXT_FLAG_RETRANSMIT : 0U),
    };
    memcpy(chunk.text, &pending.text[offset], chunk_length);

    uint8_t packet[PROTOCOL_V0_VOICE_TEXT_CHUNK_PACKET_SIZE] = {0};
    if (protocol_v0_encode_voice_text_chunk_packet(
            packet,
            sizeof(packet),
            v0_packet_next_sequence(),
            (uint64_t)now_us,
            &chunk) &&
        v0_transport_enqueue_low_priority(packet, sizeof(packet))) {
        advance_sent_chunk(slot, pending.utterance_id, chunk_count, now_us);
    }
}

static void send_status(int64_t now_us)
{
    v0_voice_link_status_t status = {0};
    v0_voice_link_get_status(&status);
    const protocol_v0_voice_status_payload_t payload = {
        .state = status.state,
        .flags = status.flags,
        .last_error = status.last_error,
        .wake_count = status.wake_count,
        .asr_success_count = status.asr_success_count,
        .asr_error_count = status.asr_error_count,
        .text_drop_count = status.text_drop_count,
    };
    uint8_t packet[PROTOCOL_V0_VOICE_STATUS_PACKET_SIZE] = {0};
    if (protocol_v0_encode_voice_status_packet(
            packet,
            sizeof(packet),
            v0_packet_next_sequence(),
            (uint64_t)now_us,
            &payload)) {
        (void)v0_transport_enqueue_low_priority(packet, sizeof(packet));
    }
}

static void voice_link_task(void *arg)
{
    (void)arg;
    int64_t next_status_us = esp_timer_get_time();
    for (;;) {
        const int64_t now_us = esp_timer_get_time();
        try_send_text_chunk(now_us);
        if (now_us >= next_status_us) {
            send_status(now_us);
            next_status_us = now_us + VOICE_STATUS_INTERVAL_US;
        }
        vTaskDelay(pdMS_TO_TICKS(20U));
    }
}

esp_err_t v0_voice_link_start(void)
{
    if (s_task_handle != NULL) {
        return ESP_OK;
    }
    v0_transport_set_rx_callback(on_transport_rx);
    if (xTaskCreate(voice_link_task,
                    "v0_voice_link",
                    4096U,
                    NULL,
                    4U,
                    &s_task_handle) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "voice BLE link started; pending=%u retry=%ums",
             VOICE_PENDING_TEXT_CAPACITY,
             (unsigned)(VOICE_RETRY_INTERVAL_US / 1000LL));
    return ESP_OK;
}

bool v0_voice_link_submit_final_text(uint64_t utterance_id,
                                     const uint8_t *utf8_text,
                                     size_t text_length)
{
    if (utf8_text == NULL || text_length == 0U ||
        text_length > PROTOCOL_V0_VOICE_TEXT_MAX_BYTES) {
        return false;
    }

    bool stored = false;
    portENTER_CRITICAL(&s_mux);
    for (size_t i = 0; i < VOICE_PENDING_TEXT_CAPACITY; ++i) {
        if (!s_pending[i].active) {
            s_pending[i].active = true;
            s_pending[i].utterance_id = utterance_id;
            s_pending[i].text_length = text_length;
            memcpy(s_pending[i].text, utf8_text, text_length);
            s_status.pending_text_count = pending_count_locked();
            stored = true;
            break;
        }
    }
    if (!stored) {
        ++s_status.text_drop_count;
    }
    portEXIT_CRITICAL(&s_mux);
    return stored;
}

void v0_voice_link_update_runtime(uint8_t state,
                                  uint8_t flags,
                                  uint16_t last_error,
                                  uint32_t wake_count,
                                  uint32_t asr_success_count,
                                  uint32_t asr_error_count)
{
    portENTER_CRITICAL(&s_mux);
    s_status.state = state;
    s_status.flags = flags;
    s_status.last_error = last_error;
    s_status.wake_count = wake_count;
    s_status.asr_success_count = asr_success_count;
    s_status.asr_error_count = asr_error_count;
    portEXIT_CRITICAL(&s_mux);
}

void v0_voice_link_get_status(v0_voice_link_status_t *out_status)
{
    if (out_status == NULL) {
        return;
    }
    portENTER_CRITICAL(&s_mux);
    *out_status = s_status;
    portEXIT_CRITICAL(&s_mux);
}
