#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "protocol_v0.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint32_t sample_index;
    uint64_t timestamp_us;
    uint16_t raw_adc;
    uint8_t flags;
} v0_ecg_sample_t;

typedef struct {
    uint32_t sample_index;
    uint64_t timestamp_us;
    protocol_v0_imu_point_t point;
    uint8_t flags;
} v0_imu_sample_t;

void v0_sample_ring_reset(void);
void v0_sample_ring_discard_pending(void);

bool v0_sample_ring_push_ecg(const v0_ecg_sample_t *sample);
bool v0_sample_ring_push_imu(const v0_imu_sample_t *sample);

size_t v0_sample_ring_pop_ecg_batch(v0_ecg_sample_t out[PROTOCOL_V0_ECG_SAMPLE_COUNT]);
size_t v0_sample_ring_pop_imu_batch(v0_imu_sample_t out[PROTOCOL_V0_IMU_SAMPLE_COUNT]);

uint8_t v0_sample_ring_ecg_usage_percent(void);
uint8_t v0_sample_ring_imu_usage_percent(void);

uint32_t v0_sample_ring_ecg_overflow_count(void);
uint32_t v0_sample_ring_imu_overflow_count(void);

#ifdef __cplusplus
}
#endif
