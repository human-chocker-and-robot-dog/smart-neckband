#pragma once

#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

esp_err_t v0_packet_task_start(void);
uint32_t v0_packet_next_sequence(void);

#ifdef __cplusplus
}
#endif
