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

static const uint8_t s_golden_imu_packet[PROTOCOL_V0_IMU_PACKET_SIZE] = {
    0x53, 0x4e, 0x01, 0x02, 0x20, 0x00, 0x05, 0x03,
    0x02, 0x01, 0x10, 0x07, 0x06, 0x05, 0x04, 0x03,
    0x02, 0x01, 0x20, 0x00, 0x00, 0x00, 0x32, 0x00,
    0x02, 0x00, 0xe8, 0x03, 0x18, 0xfc, 0x00, 0x40,
    0x0a, 0x00, 0xec, 0xff, 0x1e, 0x00, 0xe9, 0x03,
    0x19, 0xfc, 0xfc, 0x3f, 0x0b, 0x00, 0xeb, 0xff,
    0x1f, 0x00, 0x24, 0x86,
};

static const uint8_t s_golden_device_status_packet[PROTOCOL_V0_DEVICE_STATUS_PACKET_SIZE] = {
    0x53, 0x4e, 0x01, 0x03, 0x20, 0x00, 0x06, 0x03,
    0x02, 0x01, 0x00, 0x08, 0x06, 0x05, 0x04, 0x03,
    0x02, 0x01, 0x01, 0x0f, 0x19, 0x0a, 0x28, 0x00,
    0x61, 0x00, 0x03, 0x00, 0x00, 0x00, 0x01, 0x00,
    0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x03, 0x00,
    0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x05, 0x00,
    0x00, 0x00, 0xe8, 0xbd,
};

static void write_u16_le(uint8_t *out, uint16_t value)
{
    out[0] = (uint8_t)(value & 0xffU);
    out[1] = (uint8_t)((value >> 8) & 0xffU);
}

static void write_i16_le(uint8_t *out, int16_t value)
{
    write_u16_le(out, (uint16_t)value);
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

static size_t write_header(uint8_t *out,
                           uint8_t packet_type,
                           uint16_t payload_length,
                           uint32_t packet_sequence,
                           uint64_t timestamp_us)
{
    size_t offset = 0U;
    write_u16_le(&out[offset], PROTOCOL_V0_MAGIC);
    offset += 2U;
    out[offset++] = PROTOCOL_V0_VERSION;
    out[offset++] = packet_type;
    write_u16_le(&out[offset], payload_length);
    offset += 2U;
    write_u32_le(&out[offset], packet_sequence);
    offset += 4U;
    write_u64_le(&out[offset], timestamp_us);
    offset += 8U;
    return offset;
}

static bool append_crc(uint8_t *out, size_t *offset, size_t expected_size)
{
    const uint16_t crc = protocol_v0_crc16_ccitt_false(out, *offset);
    write_u16_le(&out[*offset], crc);
    *offset += 2U;
    return *offset == expected_size;
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

    size_t offset = write_header(out,
                                 PROTOCOL_V0_PACKET_TYPE_ECG_BATCH,
                                 PROTOCOL_V0_ECG_PAYLOAD_SIZE,
                                 packet_sequence,
                                 timestamp_us);

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

    return append_crc(out, &offset, PROTOCOL_V0_ECG_PACKET_SIZE);
}

bool protocol_v0_encode_imu_packet(uint8_t *out,
                                   size_t out_length,
                                   uint32_t packet_sequence,
                                   uint64_t timestamp_us,
                                   uint32_t first_sample_index,
                                   uint8_t flags,
                                   const protocol_v0_imu_point_t samples[PROTOCOL_V0_IMU_SAMPLE_COUNT])
{
    if (out == NULL || samples == NULL || out_length < PROTOCOL_V0_IMU_PACKET_SIZE) {
        return false;
    }

    size_t offset = write_header(out,
                                 PROTOCOL_V0_PACKET_TYPE_IMU_BATCH,
                                 PROTOCOL_V0_IMU_PAYLOAD_SIZE,
                                 packet_sequence,
                                 timestamp_us);

    write_u32_le(&out[offset], first_sample_index);
    offset += 4U;
    write_u16_le(&out[offset], BOARD_IMU_SAMPLE_RATE_HZ);
    offset += 2U;
    out[offset++] = PROTOCOL_V0_IMU_SAMPLE_COUNT;
    out[offset++] = flags;
    for (size_t i = 0; i < PROTOCOL_V0_IMU_SAMPLE_COUNT; ++i) {
        write_i16_le(&out[offset], samples[i].ax);
        offset += 2U;
        write_i16_le(&out[offset], samples[i].ay);
        offset += 2U;
        write_i16_le(&out[offset], samples[i].az);
        offset += 2U;
        write_i16_le(&out[offset], samples[i].gx);
        offset += 2U;
        write_i16_le(&out[offset], samples[i].gy);
        offset += 2U;
        write_i16_le(&out[offset], samples[i].gz);
        offset += 2U;
    }

    return append_crc(out, &offset, PROTOCOL_V0_IMU_PACKET_SIZE);
}

bool protocol_v0_encode_device_status_packet(uint8_t *out,
                                             size_t out_length,
                                             uint32_t packet_sequence,
                                             uint64_t timestamp_us,
                                             const protocol_v0_device_status_payload_t *status)
{
    if (out == NULL || status == NULL || out_length < PROTOCOL_V0_DEVICE_STATUS_PACKET_SIZE) {
        return false;
    }

    size_t offset = write_header(out,
                                 PROTOCOL_V0_PACKET_TYPE_DEVICE_STATUS,
                                 PROTOCOL_V0_DEVICE_STATUS_PAYLOAD_SIZE,
                                 packet_sequence,
                                 timestamp_us);

    out[offset++] = status->lead_off_flags;
    out[offset++] = status->sensor_status_flags;
    out[offset++] = status->ecg_buffer_usage_percent;
    out[offset++] = status->imu_buffer_usage_percent;
    out[offset++] = status->spp_queue_usage_percent;
    out[offset++] = 0U;
    write_u16_le(&out[offset], status->status_flags);
    offset += 2U;
    write_u32_le(&out[offset], status->error_count);
    offset += 4U;
    write_u32_le(&out[offset], status->ecg_ring_overflow_count);
    offset += 4U;
    write_u32_le(&out[offset], status->imu_ring_overflow_count);
    offset += 4U;
    write_u32_le(&out[offset], status->spp_queue_overflow_count);
    offset += 4U;
    write_u32_le(&out[offset], status->transport_drop_count);
    offset += 4U;
    write_u32_le(&out[offset], status->i2c_error_count);
    offset += 4U;

    return append_crc(out, &offset, PROTOCOL_V0_DEVICE_STATUS_PACKET_SIZE);
}

const uint8_t *protocol_v0_golden_ecg_packet(size_t *out_length)
{
    if (out_length != NULL) {
        *out_length = sizeof(s_golden_ecg_packet);
    }
    return s_golden_ecg_packet;
}

const uint8_t *protocol_v0_golden_imu_packet(size_t *out_length)
{
    if (out_length != NULL) {
        *out_length = sizeof(s_golden_imu_packet);
    }
    return s_golden_imu_packet;
}

const uint8_t *protocol_v0_golden_device_status_packet(size_t *out_length)
{
    if (out_length != NULL) {
        *out_length = sizeof(s_golden_device_status_packet);
    }
    return s_golden_device_status_packet;
}

bool protocol_v0_self_test(void)
{
    uint16_t ecg_samples[PROTOCOL_V0_ECG_SAMPLE_COUNT] = {0};
    for (size_t i = 0; i < PROTOCOL_V0_ECG_SAMPLE_COUNT; ++i) {
        ecg_samples[i] = (uint16_t)(2048U + i);
    }

    const protocol_v0_imu_point_t imu_samples[PROTOCOL_V0_IMU_SAMPLE_COUNT] = {
        {.ax = 1000, .ay = -1000, .az = 16384, .gx = 10, .gy = -20, .gz = 30},
        {.ax = 1001, .ay = -999, .az = 16380, .gx = 11, .gy = -21, .gz = 31},
    };

    const protocol_v0_device_status_payload_t status = {
        .lead_off_flags = PROTOCOL_V0_FLAG_LO_MINUS,
        .sensor_status_flags = PROTOCOL_V0_STATUS_MPU6050_ONLINE |
                               PROTOCOL_V0_STATUS_OLED_ONLINE |
                               PROTOCOL_V0_STATUS_BT_CONNECTED |
                               PROTOCOL_V0_STATUS_SAMPLING_ACTIVE,
        .ecg_buffer_usage_percent = 25U,
        .imu_buffer_usage_percent = 10U,
        .spp_queue_usage_percent = 40U,
        .status_flags = PROTOCOL_V0_FLAG_LO_MINUS |
                        PROTOCOL_V0_FLAG_SAMPLE_QUEUE_OVERFLOW |
                        PROTOCOL_V0_FLAG_TRANSPORT_OVERFLOW,
        .error_count = 3U,
        .ecg_ring_overflow_count = 1U,
        .imu_ring_overflow_count = 2U,
        .spp_queue_overflow_count = 3U,
        .transport_drop_count = 4U,
        .i2c_error_count = 5U,
    };

    uint8_t encoded_ecg[PROTOCOL_V0_ECG_PACKET_SIZE] = {0};
    uint8_t encoded_imu[PROTOCOL_V0_IMU_PACKET_SIZE] = {0};
    uint8_t encoded_status[PROTOCOL_V0_DEVICE_STATUS_PACKET_SIZE] = {0};

    const bool ecg_ok = protocol_v0_encode_ecg_packet(
        encoded_ecg,
        sizeof(encoded_ecg),
        0x01020304U,
        0x0102030405060708ULL,
        0x00001000U,
        PROTOCOL_V0_FLAG_LO_MINUS | PROTOCOL_V0_FLAG_ADC_CLIPPING,
        ecg_samples);
    const bool imu_ok = protocol_v0_encode_imu_packet(
        encoded_imu,
        sizeof(encoded_imu),
        0x01020305U,
        0x0102030405060710ULL,
        0x00000020U,
        0U,
        imu_samples);
    const bool status_ok = protocol_v0_encode_device_status_packet(
        encoded_status,
        sizeof(encoded_status),
        0x01020306U,
        0x0102030405060800ULL,
        &status);

    return ecg_ok &&
           imu_ok &&
           status_ok &&
           memcmp(encoded_ecg, s_golden_ecg_packet, sizeof(s_golden_ecg_packet)) == 0 &&
           memcmp(encoded_imu, s_golden_imu_packet, sizeof(s_golden_imu_packet)) == 0 &&
           memcmp(encoded_status,
                  s_golden_device_status_packet,
                  sizeof(s_golden_device_status_packet)) == 0;
}
