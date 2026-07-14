#include "i2c_scan.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "board_config.h"
#include "driver/i2c_master.h"
#include "esp_err.h"
#include "esp_log.h"

static const char *TAG = "v0_i2c";

esp_err_t v0_i2c_scan_once(void)
{
    i2c_master_bus_handle_t bus_handle = NULL;
    const i2c_master_bus_config_t bus_config = {
        .i2c_port = BOARD_I2C_PORT,
        .sda_io_num = BOARD_I2C_SDA_GPIO,
        .scl_io_num = BOARD_I2C_SCL_GPIO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags = {
            .enable_internal_pullup = true,
        },
    };

    ESP_LOGI(TAG, "I2C scan: SDA=GPIO%d SCL=GPIO%d range=0x%02x-0x%02x",
             BOARD_I2C_SDA_GPIO,
             BOARD_I2C_SCL_GPIO,
             BOARD_I2C_SCAN_FIRST_ADDR,
             BOARD_I2C_SCAN_LAST_ADDR);

    esp_err_t err = i2c_new_master_bus(&bus_config, &bus_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2C bus init failed: %s", esp_err_to_name(err));
        return err;
    }

    size_t found_count = 0;
    bool saw_mpu6050 = false;
    bool saw_oled = false;

    for (uint16_t address = BOARD_I2C_SCAN_FIRST_ADDR; address <= BOARD_I2C_SCAN_LAST_ADDR; ++address) {
        err = i2c_master_probe(bus_handle, address, BOARD_I2C_SCAN_TIMEOUT_MS);
        if (err == ESP_OK) {
            ++found_count;
            if (address == BOARD_MPU6050_EXPECTED_ADDR) {
                saw_mpu6050 = true;
            }
            if (address == BOARD_OLED_USUAL_ADDR) {
                saw_oled = true;
            }
            ESP_LOGI(TAG, "I2C device found at 0x%02x", address);
        } else if (err == ESP_ERR_TIMEOUT) {
            ESP_LOGW(TAG, "I2C probe timeout at 0x%02x; check pull-ups and wiring", address);
        }
    }

    ESP_LOGI(TAG, "I2C scan complete: %u device(s), MPU6050=%s, OLED_0x3c=%s",
             (unsigned)found_count,
             saw_mpu6050 ? "found" : "not found",
             saw_oled ? "found" : "not found");

    err = i2c_del_master_bus(bus_handle);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "I2C bus cleanup failed: %s", esp_err_to_name(err));
    }

    return ESP_OK;
}
