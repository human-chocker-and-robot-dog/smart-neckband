#include "acquisition_control.h"

#include <string.h>
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "packet_task.h"
#include "protocol_v0.h"
#include "sensors.h"
#include "transport.h"

#define CONTROL_SIZE 28U
#define CONTROL_TYPE 7U
#define ACK_TYPE 8U

typedef struct { uint32_t request_id; uint8_t operation; } control_request_t;
static QueueHandle_t s_requests;
static uint8_t s_rx[CONTROL_SIZE];
static size_t s_rx_size;
static int64_t s_last_rx_us;

static uint32_t read_u32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8U) |
           ((uint32_t)p[2] << 16U) | ((uint32_t)p[3] << 24U);
}

static void write_le(uint8_t *p, uint64_t value, size_t size)
{
    for (size_t i = 0; i < size; ++i) { p[i] = (uint8_t)(value >> (8U * i)); }
}

static bool decode(const uint8_t *bytes, control_request_t *request)
{
    if (bytes[0] != 0x53 || bytes[1] != 0x4e || bytes[2] != 1 ||
        bytes[3] != CONTROL_TYPE || bytes[4] != 8 || bytes[5] != 0 ||
        bytes[22] > 2 || bytes[23] || bytes[24] || bytes[25]) { return false; }
    const uint16_t crc = (uint16_t)bytes[26] | ((uint16_t)bytes[27] << 8U);
    if (crc != protocol_v0_crc16_ccitt_false(bytes, 26U)) { return false; }
    request->request_id = read_u32(&bytes[18]);
    request->operation = bytes[22];
    return true;
}

static void encode_ack(uint8_t *bytes, uint32_t sequence, uint64_t timestamp,
                       uint32_t id, bool active, uint8_t result)
{
    memset(bytes, 0, CONTROL_SIZE);
    bytes[0] = 0x53; bytes[1] = 0x4e; bytes[2] = 1; bytes[3] = ACK_TYPE; bytes[4] = 8;
    write_le(&bytes[6], sequence, 4);
    write_le(&bytes[10], timestamp, 8);
    write_le(&bytes[18], id, 4);
    bytes[22] = active ? 1 : 0;
    bytes[23] = result;
    write_le(&bytes[26], protocol_v0_crc16_ccitt_false(bytes, 26), 2);
}

esp_err_t v0_acquisition_control_init(void)
{
    if (s_requests == NULL) { s_requests = xQueueCreate(4, sizeof(control_request_t)); }
    return s_requests != NULL ? ESP_OK : ESP_ERR_NO_MEM;
}

void v0_acquisition_control_reset_rx(void)
{
    s_rx_size = 0;
    if (s_requests != NULL) { xQueueReset(s_requests); }
}

bool v0_acquisition_control_feed(const uint8_t *data, size_t length)
{
    if (data == NULL || length == 0) { return false; }
    const int64_t now = esp_timer_get_time();
    if (now - s_last_rx_us > 1000000) { s_rx_size = 0; }
    s_last_rx_us = now;
    /* Do not intercept existing V0 voice ACKs or MIC1 ASCII commands. */
    if (s_rx_size == 0) {
        if (data[0] != 0x53 || (length > 1 && data[1] != 0x4e) ||
            (length > 2 && data[2] != 1) || (length > 3 && data[3] != CONTROL_TYPE)) { return false; }
    }
    for (size_t i = 0; i < length; ++i) {
        s_rx[s_rx_size++] = data[i];
        if ((s_rx_size == 2 && s_rx[1] != 0x4e) ||
            (s_rx_size == 3 && s_rx[2] != 1) ||
            (s_rx_size == 4 && s_rx[3] != CONTROL_TYPE) ||
            (s_rx_size == 6 && (s_rx[4] != 8 || s_rx[5] != 0))) { s_rx_size = 0; return true; }
        if (s_rx_size == CONTROL_SIZE) {
            control_request_t request;
            if (decode(s_rx, &request) && s_requests != NULL) {
                /* Queue saturation deliberately produces no success ACK. */
                (void)xQueueSend(s_requests, &request, 0);
            }
            s_rx_size = 0;
        }
    }
    return true;
}

void v0_acquisition_control_process(void)
{
    control_request_t request;
    if (s_requests == NULL || xQueueReceive(s_requests, &request, 0) != pdTRUE) { return; }
    esp_err_t error = ESP_OK;
    if (request.operation != 2) { error = v0_sensors_set_active(request.operation == 1); }
    v0_sensor_status_t status = {0};
    v0_sensors_get_status(&status);
    uint8_t bytes[CONTROL_SIZE];
    encode_ack(bytes, v0_packet_next_sequence(), (uint64_t)esp_timer_get_time(),
               request.request_id, status.sampling_active, error == ESP_OK ? 0 : 1);
    (void)v0_transport_enqueue(bytes, sizeof(bytes));
}

bool v0_acquisition_control_self_test(void)
{
    /* Shared with docs/protocol/acquisition_control_golden_vectors.json. */
    static const uint8_t start[] = {
        0x53,0x4e,0x01,0x07,0x08,0x00,0,0,0,0,0,0,0,0,0,0,0,0,
        0x04,0x03,0x02,0x01,0x01,0,0,0,0x4e,0x13
    };
    static const uint8_t ack[] = {
        0x53,0x4e,0x01,0x08,0x08,0,0x05,0x03,0x02,0x01,
        0x40,0x42,0x0f,0,0,0,0,0,0x04,0x03,0x02,0x01,1,0,0,0,0x2e,0x21
    };
    uint8_t command[CONTROL_SIZE];
    memcpy(command, start, CONTROL_SIZE);
    control_request_t decoded;
    if (!decode(command, &decoded) || decoded.request_id != 0x01020304 || decoded.operation != 1) { return false; }
    command[22] ^= 1;
    if (decode(command, &decoded)) { return false; }
    uint8_t encoded[CONTROL_SIZE];
    encode_ack(encoded, 0x01020305, 1000000, 0x01020304, true, 0);
    return memcmp(encoded, ack, CONTROL_SIZE) == 0;
}
