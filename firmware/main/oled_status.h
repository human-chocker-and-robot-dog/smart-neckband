#pragma once

#include <stdbool.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

esp_err_t v0_oled_status_start(void);
bool v0_oled_status_is_online(void);

#ifdef __cplusplus
}
#endif
