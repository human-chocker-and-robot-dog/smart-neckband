#include "sample_ring.h"

#include "board_config.h"
#include "freertos/FreeRTOS.h"
#include "freertos/portmacro.h"

static v0_ecg_sample_t s_ecg_ring[BOARD_ECG_RING_CAPACITY];
static v0_imu_sample_t s_imu_ring[BOARD_IMU_RING_CAPACITY];

static size_t s_ecg_head = 0U;
static size_t s_ecg_tail = 0U;
static size_t s_ecg_count = 0U;
static uint32_t s_ecg_overflow_count = 0U;

static size_t s_imu_head = 0U;
static size_t s_imu_tail = 0U;
static size_t s_imu_count = 0U;
static uint32_t s_imu_overflow_count = 0U;

static portMUX_TYPE s_ring_mux = portMUX_INITIALIZER_UNLOCKED;

static uint8_t usage_percent(size_t count, size_t capacity)
{
    if (capacity == 0U) {
        return 0U;
    }
    return (uint8_t)((count * 100U) / capacity);
}

void v0_sample_ring_reset(void)
{
    portENTER_CRITICAL(&s_ring_mux);
    s_ecg_head = 0U;
    s_ecg_tail = 0U;
    s_ecg_count = 0U;
    s_ecg_overflow_count = 0U;
    s_imu_head = 0U;
    s_imu_tail = 0U;
    s_imu_count = 0U;
    s_imu_overflow_count = 0U;
    portEXIT_CRITICAL(&s_ring_mux);
}

bool v0_sample_ring_push_ecg(const v0_ecg_sample_t *sample)
{
    if (sample == NULL) {
        return false;
    }

    bool pushed = false;
    portENTER_CRITICAL(&s_ring_mux);
    if (s_ecg_count < BOARD_ECG_RING_CAPACITY) {
        s_ecg_ring[s_ecg_head] = *sample;
        s_ecg_head = (s_ecg_head + 1U) % BOARD_ECG_RING_CAPACITY;
        ++s_ecg_count;
        pushed = true;
    } else {
        ++s_ecg_overflow_count;
    }
    portEXIT_CRITICAL(&s_ring_mux);
    return pushed;
}

bool v0_sample_ring_push_imu(const v0_imu_sample_t *sample)
{
    if (sample == NULL) {
        return false;
    }

    bool pushed = false;
    portENTER_CRITICAL(&s_ring_mux);
    if (s_imu_count < BOARD_IMU_RING_CAPACITY) {
        s_imu_ring[s_imu_head] = *sample;
        s_imu_head = (s_imu_head + 1U) % BOARD_IMU_RING_CAPACITY;
        ++s_imu_count;
        pushed = true;
    } else {
        ++s_imu_overflow_count;
    }
    portEXIT_CRITICAL(&s_ring_mux);
    return pushed;
}

size_t v0_sample_ring_pop_ecg_batch(v0_ecg_sample_t out[PROTOCOL_V0_ECG_SAMPLE_COUNT])
{
    if (out == NULL) {
        return 0U;
    }

    portENTER_CRITICAL(&s_ring_mux);
    if (s_ecg_count < PROTOCOL_V0_ECG_SAMPLE_COUNT) {
        portEXIT_CRITICAL(&s_ring_mux);
        return 0U;
    }

    for (size_t i = 0U; i < PROTOCOL_V0_ECG_SAMPLE_COUNT; ++i) {
        out[i] = s_ecg_ring[s_ecg_tail];
        s_ecg_tail = (s_ecg_tail + 1U) % BOARD_ECG_RING_CAPACITY;
    }
    s_ecg_count -= PROTOCOL_V0_ECG_SAMPLE_COUNT;
    portEXIT_CRITICAL(&s_ring_mux);
    return PROTOCOL_V0_ECG_SAMPLE_COUNT;
}

size_t v0_sample_ring_pop_imu_batch(v0_imu_sample_t out[PROTOCOL_V0_IMU_SAMPLE_COUNT])
{
    if (out == NULL) {
        return 0U;
    }

    portENTER_CRITICAL(&s_ring_mux);
    if (s_imu_count < PROTOCOL_V0_IMU_SAMPLE_COUNT) {
        portEXIT_CRITICAL(&s_ring_mux);
        return 0U;
    }

    for (size_t i = 0U; i < PROTOCOL_V0_IMU_SAMPLE_COUNT; ++i) {
        out[i] = s_imu_ring[s_imu_tail];
        s_imu_tail = (s_imu_tail + 1U) % BOARD_IMU_RING_CAPACITY;
    }
    s_imu_count -= PROTOCOL_V0_IMU_SAMPLE_COUNT;
    portEXIT_CRITICAL(&s_ring_mux);
    return PROTOCOL_V0_IMU_SAMPLE_COUNT;
}

uint8_t v0_sample_ring_ecg_usage_percent(void)
{
    uint8_t percent = 0U;
    portENTER_CRITICAL(&s_ring_mux);
    percent = usage_percent(s_ecg_count, BOARD_ECG_RING_CAPACITY);
    portEXIT_CRITICAL(&s_ring_mux);
    return percent;
}

uint8_t v0_sample_ring_imu_usage_percent(void)
{
    uint8_t percent = 0U;
    portENTER_CRITICAL(&s_ring_mux);
    percent = usage_percent(s_imu_count, BOARD_IMU_RING_CAPACITY);
    portEXIT_CRITICAL(&s_ring_mux);
    return percent;
}

uint32_t v0_sample_ring_ecg_overflow_count(void)
{
    uint32_t count = 0U;
    portENTER_CRITICAL(&s_ring_mux);
    count = s_ecg_overflow_count;
    portEXIT_CRITICAL(&s_ring_mux);
    return count;
}

uint32_t v0_sample_ring_imu_overflow_count(void)
{
    uint32_t count = 0U;
    portENTER_CRITICAL(&s_ring_mux);
    count = s_imu_overflow_count;
    portEXIT_CRITICAL(&s_ring_mux);
    return count;
}
