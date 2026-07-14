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
#define PROTOCOL_V0_PACKET_TYPE_ECG 1U

#define PROTOCOL_V0_HEADER_SIZE 18U
#define PROTOCOL_V0_ECG_SAMPLE_COUNT 20U
#define PROTOCOL_V0_ECG_PAYLOAD_SIZE (4U + 2U + 1U + 1U + (2U * PROTOCOL_V0_ECG_SAMPLE_COUNT))
#define PROTOCOL_V0_CRC_SIZE 2U
#define PROTOCOL_V0_ECG_PACKET_SIZE (PROTOCOL_V0_HEADER_SIZE + PROTOCOL_V0_ECG_PAYLOAD_SIZE + PROTOCOL_V0_CRC_SIZE)

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

_Static_assert(sizeof(protocol_v0_packet_header_t) == PROTOCOL_V0_HEADER_SIZE,
               "protocol header size must stay wire-compatible");
_Static_assert(sizeof(protocol_v0_ecg_payload_t) == PROTOCOL_V0_ECG_PAYLOAD_SIZE,
               "ECG payload size must stay wire-compatible");

uint16_t protocol_v0_crc16_ccitt_false(const uint8_t *data, size_t length);

bool protocol_v0_encode_ecg_packet(uint8_t *out,
                                   size_t out_length,
                                   uint32_t packet_sequence,
                                   uint64_t timestamp_us,
                                   uint32_t first_sample_index,
                                   uint8_t flags,
                                   const uint16_t samples[PROTOCOL_V0_ECG_SAMPLE_COUNT]);

const uint8_t *protocol_v0_golden_ecg_packet(size_t *out_length);
bool protocol_v0_self_test(void);

#ifdef __cplusplus
}
#endif
