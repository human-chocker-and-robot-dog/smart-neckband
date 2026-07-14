#include "oled_status.h"

#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "board_config.h"
#include "driver/i2c_master.h"
#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "i2c_scan.h"
#include "protocol_v0.h"
#include "sample_ring.h"
#include "sensors.h"
#include "spp_transport.h"

static const char *TAG = "v0_oled";

static i2c_master_dev_handle_t s_oled_handle = NULL;
static TaskHandle_t s_oled_task_handle = NULL;
static volatile bool s_oled_online = false;

static uint8_t s_framebuffer[(BOARD_OLED_WIDTH * BOARD_OLED_HEIGHT) / 8U];

static void copy_glyph(uint8_t out[5], const uint8_t glyph[5])
{
    memcpy(out, glyph, 5U);
}

static void glyph_for(char c, uint8_t out[5])
{
    static const uint8_t blank[5] = {0x00, 0x00, 0x00, 0x00, 0x00};
    switch (c) {
    case '0': copy_glyph(out, (const uint8_t[5]){0x3e, 0x51, 0x49, 0x45, 0x3e}); break;
    case '1': copy_glyph(out, (const uint8_t[5]){0x00, 0x42, 0x7f, 0x40, 0x00}); break;
    case '2': copy_glyph(out, (const uint8_t[5]){0x42, 0x61, 0x51, 0x49, 0x46}); break;
    case '3': copy_glyph(out, (const uint8_t[5]){0x21, 0x41, 0x45, 0x4b, 0x31}); break;
    case '4': copy_glyph(out, (const uint8_t[5]){0x18, 0x14, 0x12, 0x7f, 0x10}); break;
    case '5': copy_glyph(out, (const uint8_t[5]){0x27, 0x45, 0x45, 0x45, 0x39}); break;
    case '6': copy_glyph(out, (const uint8_t[5]){0x3c, 0x4a, 0x49, 0x49, 0x30}); break;
    case '7': copy_glyph(out, (const uint8_t[5]){0x01, 0x71, 0x09, 0x05, 0x03}); break;
    case '8': copy_glyph(out, (const uint8_t[5]){0x36, 0x49, 0x49, 0x49, 0x36}); break;
    case '9': copy_glyph(out, (const uint8_t[5]){0x06, 0x49, 0x49, 0x29, 0x1e}); break;
    case 'A': copy_glyph(out, (const uint8_t[5]){0x7e, 0x11, 0x11, 0x11, 0x7e}); break;
    case 'B': copy_glyph(out, (const uint8_t[5]){0x7f, 0x49, 0x49, 0x49, 0x36}); break;
    case 'C': copy_glyph(out, (const uint8_t[5]){0x3e, 0x41, 0x41, 0x41, 0x22}); break;
    case 'D': copy_glyph(out, (const uint8_t[5]){0x7f, 0x41, 0x41, 0x22, 0x1c}); break;
    case 'E': copy_glyph(out, (const uint8_t[5]){0x7f, 0x49, 0x49, 0x49, 0x41}); break;
    case 'F': copy_glyph(out, (const uint8_t[5]){0x7f, 0x09, 0x09, 0x09, 0x01}); break;
    case 'G': copy_glyph(out, (const uint8_t[5]){0x3e, 0x41, 0x49, 0x49, 0x7a}); break;
    case 'H': copy_glyph(out, (const uint8_t[5]){0x7f, 0x08, 0x08, 0x08, 0x7f}); break;
    case 'I': copy_glyph(out, (const uint8_t[5]){0x00, 0x41, 0x7f, 0x41, 0x00}); break;
    case 'J': copy_glyph(out, (const uint8_t[5]){0x20, 0x40, 0x41, 0x3f, 0x01}); break;
    case 'K': copy_glyph(out, (const uint8_t[5]){0x7f, 0x08, 0x14, 0x22, 0x41}); break;
    case 'L': copy_glyph(out, (const uint8_t[5]){0x7f, 0x40, 0x40, 0x40, 0x40}); break;
    case 'M': copy_glyph(out, (const uint8_t[5]){0x7f, 0x02, 0x0c, 0x02, 0x7f}); break;
    case 'N': copy_glyph(out, (const uint8_t[5]){0x7f, 0x04, 0x08, 0x10, 0x7f}); break;
    case 'O': copy_glyph(out, (const uint8_t[5]){0x3e, 0x41, 0x41, 0x41, 0x3e}); break;
    case 'P': copy_glyph(out, (const uint8_t[5]){0x7f, 0x09, 0x09, 0x09, 0x06}); break;
    case 'Q': copy_glyph(out, (const uint8_t[5]){0x3e, 0x41, 0x51, 0x21, 0x5e}); break;
    case 'R': copy_glyph(out, (const uint8_t[5]){0x7f, 0x09, 0x19, 0x29, 0x46}); break;
    case 'S': copy_glyph(out, (const uint8_t[5]){0x46, 0x49, 0x49, 0x49, 0x31}); break;
    case 'T': copy_glyph(out, (const uint8_t[5]){0x01, 0x01, 0x7f, 0x01, 0x01}); break;
    case 'U': copy_glyph(out, (const uint8_t[5]){0x3f, 0x40, 0x40, 0x40, 0x3f}); break;
    case 'V': copy_glyph(out, (const uint8_t[5]){0x1f, 0x20, 0x40, 0x20, 0x1f}); break;
    case 'W': copy_glyph(out, (const uint8_t[5]){0x7f, 0x20, 0x18, 0x20, 0x7f}); break;
    case 'X': copy_glyph(out, (const uint8_t[5]){0x63, 0x14, 0x08, 0x14, 0x63}); break;
    case 'Y': copy_glyph(out, (const uint8_t[5]){0x03, 0x04, 0x78, 0x04, 0x03}); break;
    case 'Z': copy_glyph(out, (const uint8_t[5]){0x61, 0x51, 0x49, 0x45, 0x43}); break;
    case '%': copy_glyph(out, (const uint8_t[5]){0x23, 0x13, 0x08, 0x64, 0x62}); break;
    case '-': copy_glyph(out, (const uint8_t[5]){0x08, 0x08, 0x08, 0x08, 0x08}); break;
    default: copy_glyph(out, blank); break;
    }
}

static esp_err_t oled_transmit(const uint8_t *data, size_t length)
{
    if (s_oled_handle == NULL || data == NULL || length == 0U) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!v0_i2c_lock(pdMS_TO_TICKS(100))) {
        return ESP_ERR_TIMEOUT;
    }
    const esp_err_t err = i2c_master_transmit(s_oled_handle, data, length, BOARD_I2C_XFER_TIMEOUT_MS);
    v0_i2c_unlock();
    return err;
}

static esp_err_t oled_write_commands(const uint8_t *commands, size_t length)
{
    if (length > 31U) {
        return ESP_ERR_INVALID_SIZE;
    }
    uint8_t tx[32] = {0};
    tx[0] = 0x00U;
    memcpy(&tx[1], commands, length);
    return oled_transmit(tx, length + 1U);
}

static esp_err_t oled_init_device(void)
{
    esp_err_t err = v0_i2c_bus_init();
    if (err != ESP_OK) {
        return err;
    }

    const i2c_device_config_t device_config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = BOARD_OLED_USUAL_ADDR,
        .scl_speed_hz = BOARD_I2C_FREQUENCY_HZ,
    };
    err = i2c_master_bus_add_device(v0_i2c_bus_get(), &device_config, &s_oled_handle);
    if (err != ESP_OK) {
        return err;
    }

    const uint8_t init_commands[] = {
        0xae, 0xd5, 0x80, 0xa8, 0x1f, 0xd3, 0x00, 0x40,
        0x8d, 0x14, 0x20, 0x00, 0xa1, 0xc8, 0xda, 0x02,
        0x81, 0x8f, 0xd9, 0xf1, 0xdb, 0x40, 0xa4, 0xa6,
        0xaf,
    };
    err = oled_write_commands(init_commands, sizeof(init_commands));
    s_oled_online = err == ESP_OK;
    return err;
}

static void framebuffer_clear(void)
{
    memset(s_framebuffer, 0, sizeof(s_framebuffer));
}

static void draw_char(uint8_t x, uint8_t page, char c)
{
    if (page >= (BOARD_OLED_HEIGHT / 8U) || x >= BOARD_OLED_WIDTH) {
        return;
    }

    uint8_t glyph[5] = {0};
    glyph_for(c, glyph);

    const size_t base = (size_t)page * BOARD_OLED_WIDTH;
    for (uint8_t column = 0U; column < 5U; ++column) {
        if ((uint16_t)x + column < BOARD_OLED_WIDTH) {
            s_framebuffer[base + x + column] = glyph[column];
        }
    }
}

static void draw_text(uint8_t x, uint8_t page, const char *text)
{
    while (text != NULL && *text != '\0' && x < BOARD_OLED_WIDTH) {
        draw_char(x, page, *text);
        x = (uint8_t)(x + 6U);
        ++text;
    }
}

static esp_err_t oled_flush(void)
{
    for (uint8_t page = 0U; page < (BOARD_OLED_HEIGHT / 8U); ++page) {
        const uint8_t commands[] = {
            (uint8_t)(0xb0U + page),
            0x00,
            0x10,
        };
        esp_err_t err = oled_write_commands(commands, sizeof(commands));
        if (err != ESP_OK) {
            s_oled_online = false;
            return err;
        }

        uint8_t tx[BOARD_OLED_WIDTH + 1U] = {0};
        tx[0] = 0x40U;
        memcpy(&tx[1], &s_framebuffer[(size_t)page * BOARD_OLED_WIDTH], BOARD_OLED_WIDTH);
        err = oled_transmit(tx, sizeof(tx));
        if (err != ESP_OK) {
            s_oled_online = false;
            return err;
        }
    }
    s_oled_online = true;
    return ESP_OK;
}

static uint8_t max3(uint8_t a, uint8_t b, uint8_t c)
{
    uint8_t max_value = a > b ? a : b;
    return max_value > c ? max_value : c;
}

static uint32_t status_error_count(const v0_sensor_status_t *sensor,
                                   const v0_spp_transport_status_t *transport)
{
    return sensor->missed_timer_notifications +
           sensor->adc_error_count +
           sensor->i2c_error_count +
           v0_sample_ring_ecg_overflow_count() +
           v0_sample_ring_imu_overflow_count() +
           transport->queue_overflow_count +
           transport->write_error_count;
}

static void draw_page(bool second_page)
{
    v0_sensor_status_t sensor = {0};
    v0_spp_transport_status_t transport = {0};
    v0_sensors_get_status(&sensor);
    v0_spp_transport_get_status(&transport);

    char line1[24] = {0};
    char line2[24] = {0};

    if (!second_page) {
        snprintf(line1, sizeof(line1), "BT %s", transport.connected ? "CONN" : "WAIT");
        snprintf(line2, sizeof(line2), "ECG 500 IMU 50");
    } else {
        const uint8_t usage = max3(v0_sample_ring_ecg_usage_percent(),
                                   v0_sample_ring_imu_usage_percent(),
                                   transport.queue_usage_percent);
        snprintf(line1,
                 sizeof(line1),
                 "BUF %3u%% ERR %" PRIu32,
                 (unsigned)usage,
                 status_error_count(&sensor, &transport));
        snprintf(line2,
                 sizeof(line2),
                 "LEAD %s",
                 sensor.lead_off_flags == 0U ? "OK" : "OFF");
    }

    framebuffer_clear();
    draw_text(0U, 0U, line1);
    draw_text(0U, 2U, line2);
}

static void oled_task(void *arg)
{
    (void)arg;

    bool second_page = false;
    uint32_t page_elapsed_ms = 0U;
    for (;;) {
        if (!s_oled_online) {
            (void)oled_init_device();
        }
        if (s_oled_online) {
            draw_page(second_page);
            if (oled_flush() != ESP_OK) {
                ESP_LOGW(TAG, "OLED flush failed");
            }
        }
        vTaskDelay(pdMS_TO_TICKS(BOARD_OLED_REFRESH_INTERVAL_MS));
        page_elapsed_ms += BOARD_OLED_REFRESH_INTERVAL_MS;
        if (page_elapsed_ms >= BOARD_OLED_PAGE_INTERVAL_MS) {
            second_page = !second_page;
            page_elapsed_ms = 0U;
        }
    }
}

esp_err_t v0_oled_status_start(void)
{
    if (s_oled_task_handle != NULL) {
        return ESP_OK;
    }

    const esp_err_t err = oled_init_device();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "OLED init failed; status task will retry: %s", esp_err_to_name(err));
    } else {
        ESP_LOGI(TAG, "OLED status panel ready at 0x%02x", BOARD_OLED_USUAL_ADDR);
    }

    if (xTaskCreate(oled_task,
                    "v0_oled",
                    BOARD_OLED_TASK_STACK_BYTES,
                    NULL,
                    BOARD_OLED_TASK_PRIORITY,
                    &s_oled_task_handle) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }

    return ESP_OK;
}

bool v0_oled_status_is_online(void)
{
    return s_oled_online;
}
