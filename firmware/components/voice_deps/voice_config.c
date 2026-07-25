#include "voice_config.h"

#include <string.h>

#include "nvs.h"
#include "nvs_flash.h"

#define VOICE_CONFIG_PARTITION "voicecfg"
#define VOICE_CONFIG_NAMESPACE "voicecfg"
#define VOICE_CONFIG_SCHEMA_VERSION 1U

static esp_err_t read_string(nvs_handle_t handle,
                             const char *key,
                             char *destination,
                             size_t destination_size)
{
    size_t required_size = destination_size;
    const esp_err_t err = nvs_get_str(handle, key, destination, &required_size);
    if (err != ESP_OK) {
        return err;
    }
    if (required_size == 0U || required_size > destination_size) {
        return ESP_ERR_INVALID_SIZE;
    }
    destination[destination_size - 1U] = '\0';
    return ESP_OK;
}

esp_err_t v0_voice_config_load(v0_voice_config_t *out_config)
{
    if (out_config == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out_config, 0, sizeof(*out_config));

    const esp_err_t init_err = nvs_flash_init_partition(VOICE_CONFIG_PARTITION);
    if (init_err != ESP_OK) {
        return init_err;
    }

    nvs_handle_t handle = 0U;
    esp_err_t err = nvs_open_from_partition(
        VOICE_CONFIG_PARTITION,
        VOICE_CONFIG_NAMESPACE,
        NVS_READONLY,
        &handle);
    if (err != ESP_OK) {
        return err;
    }

    err = nvs_get_u32(handle, "schema_ver", &out_config->schema_version);
    if (err == ESP_OK) {
        err = read_string(
            handle, "wifi_ssid", out_config->wifi_ssid, sizeof(out_config->wifi_ssid));
    }
    if (err == ESP_OK) {
        err = read_string(
            handle, "wifi_pass", out_config->wifi_password, sizeof(out_config->wifi_password));
    }
    if (err == ESP_OK) {
        err = read_string(
            handle, "auth_mode", out_config->auth_mode, sizeof(out_config->auth_mode));
    }
    if (err == ESP_OK) {
        err = read_string(
            handle, "app_id", out_config->app_id, sizeof(out_config->app_id));
    }
    if (err == ESP_OK) {
        err = read_string(
            handle, "api_key", out_config->api_key, sizeof(out_config->api_key));
    }
    if (err == ESP_OK) {
        err = read_string(
            handle,
            "access_token",
            out_config->access_token,
            sizeof(out_config->access_token));
    }
    if (err == ESP_OK) {
        err = read_string(
            handle,
            "resource_id",
            out_config->resource_id,
            sizeof(out_config->resource_id));
    }
    nvs_close(handle);

    if (err != ESP_OK ||
        out_config->schema_version != VOICE_CONFIG_SCHEMA_VERSION) {
        v0_voice_config_clear(out_config);
        return err == ESP_OK ? ESP_ERR_INVALID_VERSION : err;
    }
    return ESP_OK;
}

bool v0_voice_config_is_complete(const v0_voice_config_t *config)
{
    if (config == NULL ||
        config->schema_version != VOICE_CONFIG_SCHEMA_VERSION ||
        config->wifi_ssid[0] == '\0' ||
        config->resource_id[0] == '\0') {
        return false;
    }
    if (strcmp(config->auth_mode, "api_key") == 0) {
        return config->api_key[0] != '\0';
    }
    if (strcmp(config->auth_mode, "legacy") == 0) {
        return config->app_id[0] != '\0' &&
               config->access_token[0] != '\0';
    }
    return false;
}

void v0_voice_config_clear(v0_voice_config_t *config)
{
    if (config == NULL) {
        return;
    }
    volatile uint8_t *bytes = (volatile uint8_t *)config;
    for (size_t i = 0; i < sizeof(*config); ++i) {
        bytes[i] = 0U;
    }
}
