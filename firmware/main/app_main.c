#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>

#include "board_config.h"
#include "esp_chip_info.h"
#include "esp_flash.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "esp_system.h"
#include "i2c_scan.h"
#include "protocol_v0.h"
#include "sdkconfig.h"

static const char *TAG = "v0_boot";

static const char *enabled_text(bool enabled)
{
    return enabled ? "yes" : "no";
}

static void log_partition_info(const char *label,
                               esp_partition_type_t type,
                               esp_partition_subtype_t subtype)
{
    const esp_partition_t *partition = esp_partition_find_first(type, subtype, label);
    if (partition == NULL) {
        ESP_LOGW(TAG, "partition %-8s not found", label);
        return;
    }

    ESP_LOGI(TAG,
             "partition %-8s offset=0x%06" PRIx32 " size=%" PRIu32 " bytes",
             partition->label,
             partition->address,
             partition->size);
}

static void log_chip_and_flash_info(void)
{
    esp_chip_info_t chip_info = {0};
    esp_chip_info(&chip_info);

    const unsigned major_rev = chip_info.revision / 100U;
    const unsigned minor_rev = chip_info.revision % 100U;

    uint32_t detected_flash_size = 0;
    const esp_err_t flash_err = esp_flash_get_size(NULL, &detected_flash_size);

    ESP_LOGI(TAG, "target=%s expected=%s", CONFIG_IDF_TARGET, BOARD_EXPECTED_IDF_TARGET);
    ESP_LOGI(TAG,
             "chip cores=%u revision=v%u.%u wifi=%s bt=%s ble=%s",
             (unsigned)chip_info.cores,
             major_rev,
             minor_rev,
             enabled_text((chip_info.features & CHIP_FEATURE_WIFI_BGN) != 0),
             enabled_text((chip_info.features & CHIP_FEATURE_BT) != 0),
             enabled_text((chip_info.features & CHIP_FEATURE_BLE) != 0));

    if (flash_err == ESP_OK) {
        ESP_LOGI(TAG,
                 "flash detected=%" PRIu32 " bytes configured=%u bytes",
                 detected_flash_size,
                 BOARD_CONFIGURED_FLASH_SIZE_BYTES);
        if (detected_flash_size != BOARD_CONFIGURED_FLASH_SIZE_BYTES) {
            ESP_LOGW(TAG, "detected flash size differs from configured 4 MB baseline");
        }
    } else {
        ESP_LOGE(TAG, "failed to read flash size: %s", esp_err_to_name(flash_err));
    }
}

static void log_board_config(void)
{
    ESP_LOGI(TAG, "firmware=%s board=%s", BOARD_FIRMWARE_VERSION, BOARD_NAME);
    ESP_LOGI(TAG,
             "ECG adc_gpio=GPIO%d adc1_channel=%u sample_rate=%uHz timer=%uus",
             BOARD_ECG_ADC_GPIO,
             BOARD_ECG_ADC1_CHANNEL,
             BOARD_ECG_SAMPLE_RATE_HZ,
             BOARD_ECG_TIMER_PERIOD_US);
    ESP_LOGI(TAG,
             "lead_off lo_minus=GPIO%d lo_plus=GPIO%d",
             BOARD_AD8232_LO_MINUS_GPIO,
             BOARD_AD8232_LO_PLUS_GPIO);
    ESP_LOGI(TAG,
             "I2C port=%d sda=GPIO%d scl=GPIO%d imu_rate=%uHz oled_max=%uHz",
             BOARD_I2C_PORT,
             BOARD_I2C_SDA_GPIO,
             BOARD_I2C_SCL_GPIO,
             BOARD_IMU_SAMPLE_RATE_HZ,
             BOARD_OLED_REFRESH_RATE_HZ);
}

void app_main(void)
{
    ESP_LOGI(TAG, "V0 foundation boot");
    log_board_config();
    log_chip_and_flash_info();

    log_partition_info("nvs", ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_NVS);
    log_partition_info("phy_init", ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_PHY);
    log_partition_info("factory", ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_APP_FACTORY);
    log_partition_info("storage", ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_SPIFFS);

    ESP_LOGI(TAG, "minimum free heap=%" PRIu32 " bytes", esp_get_minimum_free_heap_size());

    if (protocol_v0_self_test()) {
        ESP_LOGI(TAG, "protocol_v0 golden self-test=PASS packet_size=%u crc=CCITT-FALSE",
                 PROTOCOL_V0_ECG_PACKET_SIZE);
    } else {
        ESP_LOGE(TAG, "protocol_v0 golden self-test=FAIL");
    }

    const esp_err_t scan_err = v0_i2c_scan_once();
    if (scan_err != ESP_OK) {
        ESP_LOGW(TAG, "I2C scan did not complete cleanly: %s", esp_err_to_name(scan_err));
    }

    ESP_LOGI(TAG, "V0 foundation ready; ECG sampler and transports are not started in this build");
}
