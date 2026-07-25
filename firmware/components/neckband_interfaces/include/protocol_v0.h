#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "board_config.h"

#ifdef __cplusplus
extern "C" {
#endif

#define PROTOCOL_V0_MAGIC 0x4E53U
#define PROTOCOL_V0_VERSION 1U
#define PROTOCOL_V0_PACKET_TYPE_ECG_BATCH 1U
#define PROTOCOL_V0_PACKET_TYPE_IMU_BATCH 2U
#define PROTOCOL_V0_PACKET_TYPE_DEVICE_STATUS 3U
#define PROTOCOL_V0_PACKET_TYPE_VOICE_TEXT_CHUNK 4U
#define PROTOCOL_V0_PACKET_TYPE_VOICE_STATUS 5U
#define PROTOCOL_V0_PACKET_TYPE_VOICE_TEXT_ACK 6U

#define PROTOCOL_V0_HEADER_SIZE 18U
#define PROTOCOL_V0_ECG_SAMPLE_COUNT 20U
#define PROTOCOL_V0_IMU_SAMPLE_COUNT 2U
#define PROTOCOL_V0_ECG_PAYLOAD_SIZE (4U + 2U + 1U + 1U + (2U * PROTOCOL_V0_ECG_SAMPLE_COUNT))
#define PROTOCOL_V0_IMU_PAYLOAD_SIZE (4U + 2U + 1U + 1U + (12U * PROTOCOL_V0_IMU_SAMPLE_COUNT))
#define PROTOCOL_V0_DEVICE_STATUS_PAYLOAD_SIZE 32U
#define PROTOCOL_V0_VOICE_TEXT_DATA_SIZE 36U
#define PROTOCOL_V0_VOICE_TEXT_MAX_BYTES 512U
#define PROTOCOL_V0_VOICE_TEXT_CHUNK_PAYLOAD_SIZE 48U
#define PROTOCOL_V0_VOICE_STATUS_PAYLOAD_SIZE 20U
#define PROTOCOL_V0_VOICE_TEXT_ACK_PAYLOAD_SIZE 8U
#define PROTOCOL_V0_CRC_SIZE 2U
#define PROTOCOL_V0_ECG_PACKET_SIZE (PROTOCOL_V0_HEADER_SIZE + PROTOCOL_V0_ECG_PAYLOAD_SIZE + PROTOCOL_V0_CRC_SIZE)
#define PROTOCOL_V0_IMU_PACKET_SIZE (PROTOCOL_V0_HEADER_SIZE + PROTOCOL_V0_IMU_PAYLOAD_SIZE + PROTOCOL_V0_CRC_SIZE)
#define PROTOCOL_V0_DEVICE_STATUS_PACKET_SIZE (PROTOCOL_V0_HEADER_SIZE + PROTOCOL_V0_DEVICE_STATUS_PAYLOAD_SIZE + PROTOCOL_V0_CRC_SIZE)
#define PROTOCOL_V0_VOICE_TEXT_CHUNK_PACKET_SIZE (PROTOCOL_V0_HEADER_SIZE + PROTOCOL_V0_VOICE_TEXT_CHUNK_PAYLOAD_SIZE + PROTOCOL_V0_CRC_SIZE)
#define PROTOCOL_V0_VOICE_STATUS_PACKET_SIZE (PROTOCOL_V0_HEADER_SIZE + PROTOCOL_V0_VOICE_STATUS_PAYLOAD_SIZE + PROTOCOL_V0_CRC_SIZE)
#define PROTOCOL_V0_VOICE_TEXT_ACK_PACKET_SIZE (PROTOCOL_V0_HEADER_SIZE + PROTOCOL_V0_VOICE_TEXT_ACK_PAYLOAD_SIZE + PROTOCOL_V0_CRC_SIZE)
#define PROTOCOL_V0_MAX_PACKET_SIZE PROTOCOL_V0_ECG_PACKET_SIZE

#if PROTOCOL_V0_IMU_PACKET_SIZE > PROTOCOL_V0_MAX_PACKET_SIZE
#undef PROTOCOL_V0_MAX_PACKET_SIZE
#define PROTOCOL_V0_MAX_PACKET_SIZE PROTOCOL_V0_IMU_PACKET_SIZE
#endif

#if PROTOCOL_V0_DEVICE_STATUS_PACKET_SIZE > PROTOCOL_V0_MAX_PACKET_SIZE
#undef PROTOCOL_V0_MAX_PACKET_SIZE
#define PROTOCOL_V0_MAX_PACKET_SIZE PROTOCOL_V0_DEVICE_STATUS_PACKET_SIZE
#endif

#if PROTOCOL_V0_VOICE_TEXT_CHUNK_PACKET_SIZE > PROTOCOL_V0_MAX_PACKET_SIZE
#undef PROTOCOL_V0_MAX_PACKET_SIZE
#define PROTOCOL_V0_MAX_PACKET_SIZE PROTOCOL_V0_VOICE_TEXT_CHUNK_PACKET_SIZE
#endif

typedef enum {
    PROTOCOL_V0_FLAG_LO_MINUS = 1U << 0,
    PROTOCOL_V0_FLAG_LO_PLUS = 1U << 1,
    PROTOCOL_V0_FLAG_ADC_CLIPPING = 1U << 2,
    PROTOCOL_V0_FLAG_SAMPLE_LATE = 1U << 3,
    PROTOCOL_V0_FLAG_SAMPLE_MISSED = 1U << 4,
    PROTOCOL_V0_FLAG_SAMPLE_QUEUE_OVERFLOW = 1U << 5,
    PROTOCOL_V0_FLAG_TRANSPORT_OVERFLOW = 1U << 6,
    PROTOCOL_V0_FLAG_HISTORICAL_DATA = 1U << 7,
} protocol_v0_flags_t;

typedef enum {
    PROTOCOL_V0_STATUS_MPU6050_ONLINE = 1U << 0,
    PROTOCOL_V0_STATUS_OLED_ONLINE = 1U << 1,
    PROTOCOL_V0_STATUS_BT_CONNECTED = 1U << 2,
    PROTOCOL_V0_STATUS_SAMPLING_ACTIVE = 1U << 3,
    PROTOCOL_V0_STATUS_SPP_CONGESTED = 1U << 4,
} protocol_v0_status_flags_t;

typedef enum {
    PROTOCOL_V0_VOICE_TEXT_FLAG_FINAL = 1U << 0,
    PROTOCOL_V0_VOICE_TEXT_FLAG_RETRANSMIT = 1U << 1,
} protocol_v0_voice_text_flags_t;

typedef enum {
    PROTOCOL_V0_VOICE_STATE_DISABLED = 0U,
    PROTOCOL_V0_VOICE_STATE_IDLE_WAKE = 1U,
    PROTOCOL_V0_VOICE_STATE_ASR_CONNECTING = 2U,
    PROTOCOL_V0_VOICE_STATE_STREAMING = 3U,
    PROTOCOL_V0_VOICE_STATE_WAIT_FINAL = 4U,
    PROTOCOL_V0_VOICE_STATE_ERROR_COOLDOWN = 5U,
} protocol_v0_voice_state_t;

typedef struct __attribute__((packed)) {
    uint16_t magic;
    uint8_t protocol_version;
    uint8_t packet_type;
    uint16_t payload_length;
    uint32_t packet_sequence;
    uint64_t timestamp_us;
} protocol_v0_packet_header_t;

typedef struct __attribute__((packed)) {
    uint32_t first_sample_index;
    uint16_t sample_rate_hz;
    uint8_t sample_count;
    uint8_t flags;
    uint16_t samples[PROTOCOL_V0_ECG_SAMPLE_COUNT];
} protocol_v0_ecg_payload_t;

typedef struct __attribute__((packed)) {
    int16_t ax;
    int16_t ay;
    int16_t az;
    int16_t gx;
    int16_t gy;
    int16_t gz;
} protocol_v0_imu_point_t;

typedef struct __attribute__((packed)) {
    uint32_t first_sample_index;
    uint16_t sample_rate_hz;
    uint8_t sample_count;
    uint8_t flags;
    protocol_v0_imu_point_t samples[PROTOCOL_V0_IMU_SAMPLE_COUNT];
} protocol_v0_imu_payload_t;

typedef struct __attribute__((packed)) {
    uint8_t lead_off_flags;
    uint8_t sensor_status_flags;
    uint8_t ecg_buffer_usage_percent;
    uint8_t imu_buffer_usage_percent;
    uint8_t spp_queue_usage_percent;
    uint8_t reserved;
    uint16_t status_flags;
    uint32_t error_count;
    uint32_t ecg_ring_overflow_count;
    uint32_t imu_ring_overflow_count;
    uint32_t spp_queue_overflow_count;
    uint32_t transport_drop_count;
    uint32_t i2c_error_count;
} protocol_v0_device_status_payload_t;

typedef struct __attribute__((packed)) {
    uint64_t utterance_id;
    uint8_t chunk_index;
    uint8_t chunk_count;
    uint8_t text_length;
    uint8_t flags;
    uint8_t text[PROTOCOL_V0_VOICE_TEXT_DATA_SIZE];
} protocol_v0_voice_text_chunk_payload_t;

typedef struct __attribute__((packed)) {
    uint8_t state;
    uint8_t flags;
    uint16_t last_error;
    uint32_t wake_count;
    uint32_t asr_success_count;
    uint32_t asr_error_count;
    uint32_t text_drop_count;
} protocol_v0_voice_status_payload_t;

typedef struct __attribute__((packed)) {
    uint64_t utterance_id;
} protocol_v0_voice_text_ack_payload_t;

_Static_assert(sizeof(protocol_v0_packet_header_t) == PROTOCOL_V0_HEADER_SIZE,
               "protocol header size must stay wire-compatible");
_Static_assert(sizeof(protocol_v0_ecg_payload_t) == PROTOCOL_V0_ECG_PAYLOAD_SIZE,
               "ECG payload size must stay wire-compatible");
_Static_assert(sizeof(protocol_v0_imu_payload_t) == PROTOCOL_V0_IMU_PAYLOAD_SIZE,
               "IMU payload size must stay wire-compatible");
_Static_assert(sizeof(protocol_v0_device_status_payload_t) == PROTOCOL_V0_DEVICE_STATUS_PAYLOAD_SIZE,
               "device status payload size must stay wire-compatible");
_Static_assert(sizeof(protocol_v0_voice_text_chunk_payload_t) ==
                   PROTOCOL_V0_VOICE_TEXT_CHUNK_PAYLOAD_SIZE,
               "voice text chunk payload size must stay wire-compatible");
_Static_assert(sizeof(protocol_v0_voice_status_payload_t) ==
                   PROTOCOL_V0_VOICE_STATUS_PAYLOAD_SIZE,
               "voice status payload size must stay wire-compatible");
_Static_assert(sizeof(protocol_v0_voice_text_ack_payload_t) ==
                   PROTOCOL_V0_VOICE_TEXT_ACK_PAYLOAD_SIZE,
               "voice text ACK payload size must stay wire-compatible");

uint16_t protocol_v0_crc16_ccitt_false(const uint8_t *data, size_t length);

bool protocol_v0_encode_ecg_packet(uint8_t *out,
                                   size_t out_length,
                                   uint32_t packet_sequence,
                                   uint64_t timestamp_us,
                                   uint32_t first_sample_index,
                                   uint8_t flags,
                                   const uint16_t samples[PROTOCOL_V0_ECG_SAMPLE_COUNT]);

bool protocol_v0_encode_imu_packet(uint8_t *out,
                                   size_t out_length,
                                   uint32_t packet_sequence,
                                   uint64_t timestamp_us,
                                   uint32_t first_sample_index,
                                   uint8_t flags,
                                   const protocol_v0_imu_point_t samples[PROTOCOL_V0_IMU_SAMPLE_COUNT]);

bool protocol_v0_encode_device_status_packet(uint8_t *out,
                                             size_t out_length,
                                             uint32_t packet_sequence,
                                             uint64_t timestamp_us,
                                             const protocol_v0_device_status_payload_t *status);

bool protocol_v0_encode_voice_text_chunk_packet(
    uint8_t *out,
    size_t out_length,
    uint32_t packet_sequence,
    uint64_t timestamp_us,
    const protocol_v0_voice_text_chunk_payload_t *chunk);

bool protocol_v0_encode_voice_status_packet(
    uint8_t *out,
    size_t out_length,
    uint32_t packet_sequence,
    uint64_t timestamp_us,
    const protocol_v0_voice_status_payload_t *status);

bool protocol_v0_encode_voice_text_ack_packet(
    uint8_t *out,
    size_t out_length,
    uint32_t packet_sequence,
    uint64_t timestamp_us,
    uint64_t utterance_id);

bool protocol_v0_decode_voice_text_ack_packet(
    const uint8_t *packet,
    size_t packet_length,
    uint64_t *out_utterance_id);

const uint8_t *protocol_v0_golden_ecg_packet(size_t *out_length);
const uint8_t *protocol_v0_golden_imu_packet(size_t *out_length);
const uint8_t *protocol_v0_golden_device_status_packet(size_t *out_length);
const uint8_t *protocol_v0_golden_voice_text_chunk_packet(size_t *out_length);
const uint8_t *protocol_v0_golden_voice_status_packet(size_t *out_length);
const uint8_t *protocol_v0_golden_voice_text_ack_packet(size_t *out_length);
bool protocol_v0_self_test(void);

#ifdef __cplusplus
}
#endif
