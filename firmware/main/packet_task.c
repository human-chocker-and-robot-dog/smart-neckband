#include "packet_task.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "board_config.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "oled_status.h"
#include "protocol_v0.h"
#include "sample_ring.h"
#include "sensors.h"
#include "transport.h"

static const char *TAG = "v0_packet";

static TaskHandle_t s_packet_task_handle = NULL;
static uint32_t s_packet_sequence = 0U;

static uint32_t next_packet_sequence(void)
{
    return s_packet_sequence++;
}

static void send_ecg_batch(void)
{
    v0_ecg_sample_t batch[PROTOCOL_V0_ECG_SAMPLE_COUNT] = {0};
    if (v0_sample_ring_pop_ecg_batch(batch) != PROTOCOL_V0_ECG_SAMPLE_COUNT) {
        return;
    }

    uint16_t samples[PROTOCOL_V0_ECG_SAMPLE_COUNT] = {0};
    uint8_t flags = 0U;
    for (size_t i = 0U; i < PROTOCOL_V0_ECG_SAMPLE_COUNT; ++i) {
        samples[i] = batch[i].raw_adc;
        flags |= batch[i].flags;
    }

    uint8_t packet[PROTOCOL_V0_ECG_PACKET_SIZE] = {0};
    if (protocol_v0_encode_ecg_packet(packet,
                                      sizeof(packet),
                                      next_packet_sequence(),
                                      batch[0].timestamp_us,
                                      batch[0].sample_index,
                                      flags,
                                      samples)) {
        (void)v0_transport_enqueue(packet, sizeof(packet));
    }
}

static void send_imu_batch(void)
{
    v0_imu_sample_t batch[PROTOCOL_V0_IMU_SAMPLE_COUNT] = {0};
    if (v0_sample_ring_pop_imu_batch(batch) != PROTOCOL_V0_IMU_SAMPLE_COUNT) {
        return;
    }

    protocol_v0_imu_point_t samples[PROTOCOL_V0_IMU_SAMPLE_COUNT] = {0};
    uint8_t flags = 0U;
    for (size_t i = 0U; i < PROTOCOL_V0_IMU_SAMPLE_COUNT; ++i) {
        samples[i] = batch[i].point;
        flags |= batch[i].flags;
    }

    uint8_t packet[PROTOCOL_V0_IMU_PACKET_SIZE] = {0};
    if (protocol_v0_encode_imu_packet(packet,
                                      sizeof(packet),
                                      next_packet_sequence(),
                                      batch[0].timestamp_us,
                                      batch[0].sample_index,
                                      flags,
                                      samples)) {
        (void)v0_transport_enqueue(packet, sizeof(packet));
    }
}

static uint32_t total_error_count(const v0_sensor_status_t *sensor,
                                  const v0_transport_status_t *transport)
{
    return sensor->missed_timer_notifications +
           sensor->adc_error_count +
           sensor->i2c_error_count +
           v0_sample_ring_ecg_overflow_count() +
           v0_sample_ring_imu_overflow_count() +
           transport->queue_overflow_count +
           transport->write_error_count;
}

static uint16_t aggregate_status_flags(const v0_sensor_status_t *sensor,
                                       const v0_transport_status_t *transport)
{
    uint16_t flags = sensor->lead_off_flags;
    if (sensor->missed_timer_notifications > 0U) {
        flags |= PROTOCOL_V0_FLAG_SAMPLE_MISSED;
    }
    if (v0_sample_ring_ecg_overflow_count() > 0U ||
        v0_sample_ring_imu_overflow_count() > 0U) {
        flags |= PROTOCOL_V0_FLAG_SAMPLE_QUEUE_OVERFLOW;
    }
    if (transport->queue_overflow_count > 0U ||
        transport->write_error_count > 0U) {
        flags |= PROTOCOL_V0_FLAG_TRANSPORT_OVERFLOW;
    }
    return flags;
}

static void send_device_status(void)
{
    v0_sensor_status_t sensor = {0};
    v0_transport_status_t transport = {0};
    v0_sensors_get_status(&sensor);
    v0_transport_get_status(&transport);

    uint8_t sensor_flags = 0U;
    if (sensor.mpu6050_online) {
        sensor_flags |= PROTOCOL_V0_STATUS_MPU6050_ONLINE;
    }
    if (v0_oled_status_is_online()) {
        sensor_flags |= PROTOCOL_V0_STATUS_OLED_ONLINE;
    }
    if (transport.connected) {
        sensor_flags |= PROTOCOL_V0_STATUS_BT_CONNECTED;
    }
    if (sensor.sampling_active) {
        sensor_flags |= PROTOCOL_V0_STATUS_SAMPLING_ACTIVE;
    }
    if (transport.congested) {
        sensor_flags |= PROTOCOL_V0_STATUS_SPP_CONGESTED;
    }

    const protocol_v0_device_status_payload_t status = {
        .lead_off_flags = sensor.lead_off_flags,
        .sensor_status_flags = sensor_flags,
        .ecg_buffer_usage_percent = v0_sample_ring_ecg_usage_percent(),
        .imu_buffer_usage_percent = v0_sample_ring_imu_usage_percent(),
        .spp_queue_usage_percent = transport.queue_usage_percent,
        .status_flags = aggregate_status_flags(&sensor, &transport),
        .error_count = total_error_count(&sensor, &transport),
        .ecg_ring_overflow_count = v0_sample_ring_ecg_overflow_count(),
        .imu_ring_overflow_count = v0_sample_ring_imu_overflow_count(),
        .spp_queue_overflow_count = transport.queue_overflow_count,
        .transport_drop_count = transport.disconnected_drop_count,
        .i2c_error_count = sensor.i2c_error_count,
    };

    uint8_t packet[PROTOCOL_V0_DEVICE_STATUS_PACKET_SIZE] = {0};
    if (protocol_v0_encode_device_status_packet(packet,
                                                sizeof(packet),
                                                next_packet_sequence(),
                                                (uint64_t)esp_timer_get_time(),
                                                &status)) {
        (void)v0_transport_enqueue(packet, sizeof(packet));
    }
}

static void packet_task(void *arg)
{
    (void)arg;

    int64_t next_status_us = esp_timer_get_time();
    for (;;) {
        send_ecg_batch();
        send_imu_batch();

        const int64_t now_us = esp_timer_get_time();
        if (now_us >= next_status_us) {
            send_device_status();
            next_status_us = now_us + 1000000LL;
        }

        /* The default C3 tick is 10 ms. A 2 ms conversion becomes zero and
         * turns this priority-7 loop into a busy yield that starves IDLE and
         * triggers the single-core task watchdog. One tick is still well
         * below the 40 ms ECG/IMU packet period. */
        vTaskDelay(pdMS_TO_TICKS(10U));
    }
}

esp_err_t v0_packet_task_start(void)
{
    if (s_packet_task_handle != NULL) {
        return ESP_OK;
    }

    if (xTaskCreate(packet_task,
                    "v0_packet",
                    BOARD_PACKET_TASK_STACK_BYTES,
                    NULL,
                    BOARD_PACKET_TASK_PRIORITY,
                    &s_packet_task_handle) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(TAG, "packet task started");
    return ESP_OK;
}
