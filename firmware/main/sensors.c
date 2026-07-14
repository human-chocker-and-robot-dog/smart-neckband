#include "sensors.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "board_config.h"
#include "driver/gpio.h"
#include "driver/gptimer.h"
#include "driver/i2c_master.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "i2c_scan.h"
#include "protocol_v0.h"
#include "sample_ring.h"

static const char *TAG = "v0_sensors";

static const uint8_t MPU6050_REG_SMPLRT_DIV = 0x19U;
static const uint8_t MPU6050_REG_CONFIG = 0x1AU;
static const uint8_t MPU6050_REG_GYRO_CONFIG = 0x1BU;
static const uint8_t MPU6050_REG_ACCEL_CONFIG = 0x1CU;
static const uint8_t MPU6050_REG_ACCEL_XOUT_H = 0x3BU;
static const uint8_t MPU6050_REG_PWR_MGMT_1 = 0x6BU;
static const uint8_t MPU6050_REG_WHO_AM_I = 0x75U;

static adc_oneshot_unit_handle_t s_adc_handle = NULL;
static gptimer_handle_t s_ecg_timer = NULL;
static TaskHandle_t s_ecg_task_handle = NULL;
static TaskHandle_t s_imu_task_handle = NULL;
static i2c_master_dev_handle_t s_mpu6050_handle = NULL;

static portMUX_TYPE s_status_mux = portMUX_INITIALIZER_UNLOCKED;
static bool s_sampling_active = false;
static bool s_mpu6050_online = false;
static uint8_t s_lead_off_flags = 0U;
static uint32_t s_missed_timer_notifications = 0U;
static uint32_t s_adc_error_count = 0U;
static uint32_t s_i2c_error_count = 0U;

static uint32_t s_ecg_sample_index = 0U;
static uint32_t s_imu_sample_index = 0U;

static void set_mpu6050_online(bool online)
{
    portENTER_CRITICAL(&s_status_mux);
    s_mpu6050_online = online;
    portEXIT_CRITICAL(&s_status_mux);
}

static void set_lead_off_flags(uint8_t flags)
{
    portENTER_CRITICAL(&s_status_mux);
    s_lead_off_flags = flags;
    portEXIT_CRITICAL(&s_status_mux);
}

static void add_missed_notifications(uint32_t count)
{
    portENTER_CRITICAL(&s_status_mux);
    s_missed_timer_notifications += count;
    portEXIT_CRITICAL(&s_status_mux);
}

static void add_adc_error(void)
{
    portENTER_CRITICAL(&s_status_mux);
    ++s_adc_error_count;
    portEXIT_CRITICAL(&s_status_mux);
}

static void add_i2c_error(void)
{
    portENTER_CRITICAL(&s_status_mux);
    ++s_i2c_error_count;
    portEXIT_CRITICAL(&s_status_mux);
}

static uint8_t read_lead_off_flags(void)
{
    uint8_t flags = 0U;
    if (gpio_get_level(BOARD_AD8232_LO_MINUS_GPIO) != 0) {
        flags |= PROTOCOL_V0_FLAG_LO_MINUS;
    }
    if (gpio_get_level(BOARD_AD8232_LO_PLUS_GPIO) != 0) {
        flags |= PROTOCOL_V0_FLAG_LO_PLUS;
    }
    set_lead_off_flags(flags);
    return flags;
}

static esp_err_t init_lead_off_gpio(void)
{
    const gpio_config_t input_config = {
        .pin_bit_mask = (1ULL << BOARD_AD8232_LO_MINUS_GPIO) |
                        (1ULL << BOARD_AD8232_LO_PLUS_GPIO),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    return gpio_config(&input_config);
}

static esp_err_t init_adc(void)
{
    const adc_oneshot_unit_init_cfg_t unit_config = {
        .unit_id = ADC_UNIT_1,
    };
    esp_err_t err = adc_oneshot_new_unit(&unit_config, &s_adc_handle);
    if (err != ESP_OK) {
        return err;
    }

    const adc_oneshot_chan_cfg_t channel_config = {
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    return adc_oneshot_config_channel(s_adc_handle, BOARD_ECG_ADC_CHANNEL, &channel_config);
}

static bool IRAM_ATTR ecg_timer_on_alarm(gptimer_handle_t timer,
                                         const gptimer_alarm_event_data_t *edata,
                                         void *user_ctx)
{
    (void)timer;
    (void)edata;
    (void)user_ctx;

    BaseType_t high_task_woken = pdFALSE;
    if (s_ecg_task_handle != NULL) {
        vTaskNotifyGiveFromISR(s_ecg_task_handle, &high_task_woken);
    }
    return high_task_woken == pdTRUE;
}

static esp_err_t start_ecg_timer(void)
{
    const gptimer_config_t timer_config = {
        .clk_src = GPTIMER_CLK_SRC_DEFAULT,
        .direction = GPTIMER_COUNT_UP,
        .resolution_hz = 1000000U,
    };
    esp_err_t err = gptimer_new_timer(&timer_config, &s_ecg_timer);
    if (err != ESP_OK) {
        return err;
    }

    const gptimer_event_callbacks_t callbacks = {
        .on_alarm = ecg_timer_on_alarm,
    };
    err = gptimer_register_event_callbacks(s_ecg_timer, &callbacks, NULL);
    if (err != ESP_OK) {
        return err;
    }

    const gptimer_alarm_config_t alarm_config = {
        .alarm_count = BOARD_ECG_TIMER_PERIOD_US,
        .reload_count = 0U,
        .flags = {
            .auto_reload_on_alarm = true,
        },
    };
    err = gptimer_set_alarm_action(s_ecg_timer, &alarm_config);
    if (err != ESP_OK) {
        return err;
    }
    err = gptimer_enable(s_ecg_timer);
    if (err != ESP_OK) {
        return err;
    }
    return gptimer_start(s_ecg_timer);
}

static esp_err_t mpu6050_write_reg(uint8_t reg, uint8_t value)
{
    uint8_t payload[2] = {reg, value};
    if (!v0_i2c_lock(pdMS_TO_TICKS(100))) {
        add_i2c_error();
        return ESP_ERR_TIMEOUT;
    }
    const esp_err_t err = i2c_master_transmit(s_mpu6050_handle,
                                              payload,
                                              sizeof(payload),
                                              pdMS_TO_TICKS(50));
    v0_i2c_unlock();
    if (err != ESP_OK) {
        add_i2c_error();
    }
    return err;
}

static esp_err_t mpu6050_read_reg(uint8_t reg, uint8_t *out, size_t out_length)
{
    if (out == NULL || out_length == 0U) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!v0_i2c_lock(pdMS_TO_TICKS(100))) {
        add_i2c_error();
        return ESP_ERR_TIMEOUT;
    }
    const esp_err_t err = i2c_master_transmit_receive(s_mpu6050_handle,
                                                      &reg,
                                                      1U,
                                                      out,
                                                      out_length,
                                                      pdMS_TO_TICKS(50));
    v0_i2c_unlock();
    if (err != ESP_OK) {
        add_i2c_error();
    }
    return err;
}

static esp_err_t prepare_mpu6050_device(void)
{
    if (s_mpu6050_handle != NULL) {
        return ESP_OK;
    }

    esp_err_t err = v0_i2c_bus_init();
    if (err != ESP_OK) {
        return err;
    }

    const i2c_device_config_t device_config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = BOARD_MPU6050_EXPECTED_ADDR,
        .scl_speed_hz = BOARD_I2C_FREQUENCY_HZ,
    };
    err = i2c_master_bus_add_device(v0_i2c_bus_get(), &device_config, &s_mpu6050_handle);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "MPU6050 device add failed: %s", esp_err_to_name(err));
    }
    return err;
}

static esp_err_t configure_mpu6050(void)
{
    esp_err_t err = prepare_mpu6050_device();
    if (err != ESP_OK) {
        set_mpu6050_online(false);
        return err;
    }

    uint8_t who_am_i = 0U;
    err = mpu6050_read_reg(MPU6050_REG_WHO_AM_I, &who_am_i, sizeof(who_am_i));
    if (err != ESP_OK || who_am_i != BOARD_MPU6050_EXPECTED_ADDR) {
        ESP_LOGW(TAG,
                 "MPU6050 WHO_AM_I failed: err=%s value=0x%02x",
                 esp_err_to_name(err),
                 who_am_i);
        set_mpu6050_online(false);
        return err == ESP_OK ? ESP_ERR_NOT_FOUND : err;
    }

    err = mpu6050_write_reg(MPU6050_REG_PWR_MGMT_1, 0x00U);
    if (err == ESP_OK) {
        err = mpu6050_write_reg(MPU6050_REG_SMPLRT_DIV, 19U);
    }
    if (err == ESP_OK) {
        err = mpu6050_write_reg(MPU6050_REG_CONFIG, 0x03U);
    }
    if (err == ESP_OK) {
        err = mpu6050_write_reg(MPU6050_REG_GYRO_CONFIG, 0x00U);
    }
    if (err == ESP_OK) {
        err = mpu6050_write_reg(MPU6050_REG_ACCEL_CONFIG, 0x00U);
    }

    set_mpu6050_online(err == ESP_OK);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "MPU6050 configured for %u Hz raw six-axis reads", BOARD_IMU_SAMPLE_RATE_HZ);
    } else {
        ESP_LOGW(TAG, "MPU6050 configure failed: %s", esp_err_to_name(err));
    }
    return err;
}

static int16_t read_i16_be(const uint8_t *data)
{
    return (int16_t)(((uint16_t)data[0] << 8U) | data[1]);
}

static esp_err_t read_mpu6050_sample(protocol_v0_imu_point_t *out_point)
{
    if (out_point == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    uint8_t raw[14] = {0};
    const esp_err_t err = mpu6050_read_reg(MPU6050_REG_ACCEL_XOUT_H, raw, sizeof(raw));
    if (err != ESP_OK) {
        set_mpu6050_online(false);
        return err;
    }

    out_point->ax = read_i16_be(&raw[0]);
    out_point->ay = read_i16_be(&raw[2]);
    out_point->az = read_i16_be(&raw[4]);
    out_point->gx = read_i16_be(&raw[8]);
    out_point->gy = read_i16_be(&raw[10]);
    out_point->gz = read_i16_be(&raw[12]);
    set_mpu6050_online(true);
    return ESP_OK;
}

static void ecg_task(void *arg)
{
    (void)arg;

    for (;;) {
        const uint32_t notifications = ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        uint8_t flags = read_lead_off_flags();

        if (notifications > 1U) {
            add_missed_notifications(notifications - 1U);
            flags |= PROTOCOL_V0_FLAG_SAMPLE_MISSED;
        }

        int raw = 0;
        const int64_t timestamp = esp_timer_get_time();
        const esp_err_t err = adc_oneshot_read(s_adc_handle, BOARD_ECG_ADC_CHANNEL, &raw);
        if (err != ESP_OK) {
            add_adc_error();
            continue;
        }

        if (raw <= 0 || raw >= 4095) {
            flags |= PROTOCOL_V0_FLAG_ADC_CLIPPING;
        }

        const v0_ecg_sample_t sample = {
            .sample_index = s_ecg_sample_index++,
            .timestamp_us = (uint64_t)timestamp,
            .raw_adc = (uint16_t)raw,
            .flags = flags,
        };
        (void)v0_sample_ring_push_ecg(&sample);
    }
}

static void imu_task(void *arg)
{
    (void)arg;

    TickType_t last_wake = xTaskGetTickCount();
    const TickType_t period = pdMS_TO_TICKS(1000U / BOARD_IMU_SAMPLE_RATE_HZ);
    uint32_t retry_divider = 0U;

    for (;;) {
        vTaskDelayUntil(&last_wake, period);

        bool online = false;
        portENTER_CRITICAL(&s_status_mux);
        online = s_mpu6050_online;
        portEXIT_CRITICAL(&s_status_mux);

        if (!online && (retry_divider++ % BOARD_IMU_SAMPLE_RATE_HZ) == 0U) {
            (void)configure_mpu6050();
        }

        protocol_v0_imu_point_t point = {0};
        if (read_mpu6050_sample(&point) != ESP_OK) {
            continue;
        }

        const v0_imu_sample_t sample = {
            .sample_index = s_imu_sample_index++,
            .timestamp_us = (uint64_t)esp_timer_get_time(),
            .point = point,
            .flags = 0U,
        };
        (void)v0_sample_ring_push_imu(&sample);
    }
}

esp_err_t v0_sensors_start(void)
{
    v0_sample_ring_reset();

    esp_err_t err = init_lead_off_gpio();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "lead-off GPIO init failed: %s", esp_err_to_name(err));
        return err;
    }

    err = init_adc();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ADC init failed: %s", esp_err_to_name(err));
        return err;
    }

    (void)configure_mpu6050();

    if (xTaskCreate(ecg_task,
                    "v0_ecg",
                    BOARD_ECG_TASK_STACK_BYTES,
                    NULL,
                    BOARD_ECG_TASK_PRIORITY,
                    &s_ecg_task_handle) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }

    if (xTaskCreate(imu_task,
                    "v0_imu",
                    BOARD_IMU_TASK_STACK_BYTES,
                    NULL,
                    BOARD_IMU_TASK_PRIORITY,
                    &s_imu_task_handle) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }

    err = start_ecg_timer();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ECG GPTimer start failed: %s", esp_err_to_name(err));
        return err;
    }

    portENTER_CRITICAL(&s_status_mux);
    s_sampling_active = true;
    portEXIT_CRITICAL(&s_status_mux);
    ESP_LOGI(TAG,
             "sampling started: ECG=%u Hz raw ADC1_CH%u, IMU=%u Hz raw six-axis",
             BOARD_ECG_SAMPLE_RATE_HZ,
             BOARD_ECG_ADC1_CHANNEL,
             BOARD_IMU_SAMPLE_RATE_HZ);
    return ESP_OK;
}

void v0_sensors_get_status(v0_sensor_status_t *out_status)
{
    if (out_status == NULL) {
        return;
    }

    portENTER_CRITICAL(&s_status_mux);
    out_status->sampling_active = s_sampling_active;
    out_status->mpu6050_online = s_mpu6050_online;
    out_status->lead_off_flags = s_lead_off_flags;
    out_status->missed_timer_notifications = s_missed_timer_notifications;
    out_status->adc_error_count = s_adc_error_count;
    out_status->i2c_error_count = s_i2c_error_count;
    portEXIT_CRITICAL(&s_status_mux);
}
