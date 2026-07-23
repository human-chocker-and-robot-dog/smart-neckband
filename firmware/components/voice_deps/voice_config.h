#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define V0_VOICE_WIFI_SSID_MAX 32U
#define V0_VOICE_WIFI_PASSWORD_MAX 64U
#define V0_VOICE_CREDENTIAL_MAX 128U
#define V0_VOICE_RESOURCE_ID_MAX 64U

typedef struct {
    uint32_t schema_version;
    char wifi_ssid[V0_VOICE_WIFI_SSID_MAX + 1U];
    char wifi_password[V0_VOICE_WIFI_PASSWORD_MAX + 1U];
    char auth_mode[16U];
    char app_id[V0_VOICE_CREDENTIAL_MAX + 1U];
    char api_key[V0_VOICE_CREDENTIAL_MAX + 1U];
    char access_token[V0_VOICE_CREDENTIAL_MAX + 1U];
    char resource_id[V0_VOICE_RESOURCE_ID_MAX + 1U];
} v0_voice_config_t;

esp_err_t v0_voice_config_load(v0_voice_config_t *out_config);
bool v0_voice_config_is_complete(const v0_voice_config_t *config);
void v0_voice_config_clear(v0_voice_config_t *config);

#ifdef __cplusplus
}
#endif
