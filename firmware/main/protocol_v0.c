#include "protocol_v0.h"

#include <string.h>

static const uint8_t s_golden_ecg_packet[PROTOCOL_V0_ECG_PACKET_SIZE] = {
    0x53, 0x4e, 0x01, 0x01, 0x30, 0x00, 0x04, 0x03,
    0x02, 0x01, 0x08, 0x07, 0x06, 0x05, 0x04, 0x03,
    0x02, 0x01, 0x00, 0x10, 0x00, 0x00, 0xf4, 0x01,
    0x14, 0x05, 0x00, 0x08, 0x01, 0x08, 0x02, 0x08,
    0x03, 0x08, 0x04, 0x08, 0x05, 0x08, 0x06, 0x08,
    0x07, 0x08, 0x08, 0x08, 0x09, 0x08, 0x0a, 0x08,
    0x0b, 0x08, 0x0c, 0x08, 0x0d, 0x08, 0x0e, 0x08,
    0x0f, 0x08, 0x10, 0x08, 0x11, 0x08, 0x12, 0x08,
    0x13, 0x08, 0x79, 0xa9,
};

static void write_u16_le(uint8_t *out, uint16_t value)
{
    out[0] = (uint8_t)(value & 0xffU);
    out[1] = (uint8_t)((value >> 8) & 0xffU);
}

static void write_u32_le(uint8_t *out, uint32_t value)
{
    out[0] = (uint8_t)(value & 0xffU);
    out[1] = (uint8_t)((value >> 8) & 0xffU);
    out[2] = (uint8_t)((value >> 16) & 0xffU);
    out[3] = (uint8_t)((value >> 24) & 0xffU);
}

static void write_u64_le(uint8_t *out, uint64_t value)
{
    for (size_t i = 0; i < 8U; ++i) {
        out[i] = (uint8_t)((value >> (8U * i)) & 0xffU);
    }
}

uint16_t protocol_v0_crc16_ccitt_false(const uint8_t *data, size_t length)
{
    if (data == NULL && length > 0U) {
        return 0U;
    }

    uint16_t crc = 0xffffU;
    for (size_t i = 0; i < length; ++i) {
        crc ^= (uint16_t)data[i] << 8U;
        for (size_t bit = 0; bit < 8U; ++bit) {
            if ((crc & 0x8000U) != 0U) {
                crc = (uint16_t)((crc << 1U) ^ 0x1021U);
            } else {
                crc = (uint16_t)(crc << 1U);
            }
        }
    }
    return crc;
}

bool protocol_v0_encode_ecg_packet(uint8_t *out,
                                   size_t out_length,
                                   uint32_t packet_sequence,
                                   uint64_t timestamp_us,
                                   uint32_t first_sample_index,
                                   uint8_t flags,
                                   const uint16_t samples[PROTOCOL_V0_ECG_SAMPLE_COUNT])
{
    if (out == NULL || samples == NULL || out_length < PROTOCOL_V0_ECG_PACKET_SIZE) {
        return false;
    }

    size_t offset = 0U;
    write_u16_le(&out[offset], PROTOCOL_V0_MAGIC);
    offset += 2U;
    out[offset++] = PROTOCOL_V0_VERSION;
    out[offset++] = PROTOCOL_V0_PACKET_TYPE_ECG;
    write_u16_le(&out[offset], PROTOCOL_V0_ECG_PAYLOAD_SIZE);
    offset += 2U;
    write_u32_le(&out[offset], packet_sequence);
    offset += 4U;
    write_u64_le(&out[offset], timestamp_us);
    offset += 8U;

    write_u32_le(&out[offset], first_sample_index);
    offset += 4U;
    write_u16_le(&out[offset], BOARD_ECG_SAMPLE_RATE_HZ);
    offset += 2U;
    out[offset++] = PROTOCOL_V0_ECG_SAMPLE_COUNT;
    out[offset++] = flags;
    for (size_t i = 0; i < PROTOCOL_V0_ECG_SAMPLE_COUNT; ++i) {
        write_u16_le(&out[offset], samples[i]);
        offset += 2U;
    }

    const uint16_t crc = protocol_v0_crc16_ccitt_false(out, offset);
    write_u16_le(&out[offset], crc);
    offset += 2U;

    return offset == PROTOCOL_V0_ECG_PACKET_SIZE;
}

const uint8_t *protocol_v0_golden_ecg_packet(size_t *out_length)
{
    if (out_length != NULL) {
        *out_length = sizeof(s_golden_ecg_packet);
    }
    return s_golden_ecg_packet;
}

bool protocol_v0_self_test(void)
{
    uint16_t samples[PROTOCOL_V0_ECG_SAMPLE_COUNT] = {0};
    for (size_t i = 0; i < PROTOCOL_V0_ECG_SAMPLE_COUNT; ++i) {
        samples[i] = (uint16_t)(2048U + i);
    }

    uint8_t encoded[PROTOCOL_V0_ECG_PACKET_SIZE] = {0};
    const bool encoded_ok = protocol_v0_encode_ecg_packet(
        encoded,
        sizeof(encoded),
        0x01020304U,
        0x0102030405060708ULL,
        0x00001000U,
        PROTOCOL_V0_FLAG_LO_MINUS | PROTOCOL_V0_FLAG_ADC_CLIPPING,
        samples);

    return encoded_ok &&
           memcmp(encoded, s_golden_ecg_packet, sizeof(s_golden_ecg_packet)) == 0;
}
