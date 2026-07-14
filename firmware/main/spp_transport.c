#include "spp_transport.h"

#include <stdbool.h>
#include <inttypes.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "board_config.h"
#include "esp_bt.h"
#include "esp_bt_device.h"
#include "esp_bt_main.h"
#include "esp_err.h"
#include "esp_gap_bt_api.h"
#include "esp_log.h"
#include "esp_spp_api.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "protocol_v0.h"

static const char *TAG = "v0_spp";

typedef struct {
    uint16_t length;
    uint8_t data[PROTOCOL_V0_MAX_PACKET_SIZE];
} v0_spp_tx_item_t;

static QueueHandle_t s_tx_queue = NULL;
static TaskHandle_t s_tx_task_handle = NULL;

static volatile bool s_connected = false;
static volatile bool s_congested = false;
static volatile bool s_write_pending = false;
static uint32_t s_client_handle = 0U;

static portMUX_TYPE s_status_mux = portMUX_INITIALIZER_UNLOCKED;
static uint32_t s_queue_overflow_count = 0U;
static uint32_t s_disconnected_drop_count = 0U;
static uint32_t s_write_error_count = 0U;

static void notify_tx_task(void)
{
    if (s_tx_task_handle != NULL) {
        xTaskNotifyGive(s_tx_task_handle);
    }
}

static void add_queue_overflow(void)
{
    portENTER_CRITICAL(&s_status_mux);
    ++s_queue_overflow_count;
    portEXIT_CRITICAL(&s_status_mux);
}

static void add_disconnected_drop(void)
{
    portENTER_CRITICAL(&s_status_mux);
    ++s_disconnected_drop_count;
    portEXIT_CRITICAL(&s_status_mux);
}

static void add_write_error(void)
{
    portENTER_CRITICAL(&s_status_mux);
    ++s_write_error_count;
    portEXIT_CRITICAL(&s_status_mux);
}

static void gap_callback(esp_bt_gap_cb_event_t event, esp_bt_gap_cb_param_t *param)
{
    switch (event) {
    case ESP_BT_GAP_AUTH_CMPL_EVT:
        if (param->auth_cmpl.stat == ESP_BT_STATUS_SUCCESS) {
            ESP_LOGI(TAG, "paired with %s", param->auth_cmpl.device_name);
        } else {
            ESP_LOGW(TAG, "pairing failed status=%d", param->auth_cmpl.stat);
        }
        break;
    default:
        break;
    }
}

static void spp_callback(esp_spp_cb_event_t event, esp_spp_cb_param_t *param)
{
    switch (event) {
    case ESP_SPP_INIT_EVT:
        ESP_LOGI(TAG, "SPP init status=%d", param->init.status);
        (void)esp_bt_gap_set_device_name(BOARD_SPP_DEVICE_NAME);
        (void)esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE);
        (void)esp_spp_start_srv(ESP_SPP_SEC_AUTHENTICATE,
                                ESP_SPP_ROLE_SLAVE,
                                0U,
                                BOARD_SPP_SERVER_NAME);
        break;
    case ESP_SPP_START_EVT:
        ESP_LOGI(TAG, "SPP server started handle=%" PRIu32, param->start.handle);
        break;
    case ESP_SPP_SRV_OPEN_EVT:
        s_client_handle = param->srv_open.handle;
        s_connected = true;
        s_congested = false;
        s_write_pending = false;
        ESP_LOGI(TAG, "SPP client connected handle=%" PRIu32, s_client_handle);
        notify_tx_task();
        break;
    case ESP_SPP_CLOSE_EVT:
        ESP_LOGI(TAG, "SPP client disconnected handle=%" PRIu32, param->close.handle);
        s_connected = false;
        s_congested = false;
        s_write_pending = false;
        s_client_handle = 0U;
        notify_tx_task();
        break;
    case ESP_SPP_CONG_EVT:
        s_congested = param->cong.cong;
        ESP_LOGI(TAG, "SPP congestion=%s", s_congested ? "true" : "false");
        notify_tx_task();
        break;
    case ESP_SPP_WRITE_EVT:
        if (param->write.status != ESP_SPP_SUCCESS) {
            add_write_error();
            ESP_LOGW(TAG, "SPP write failed status=%d", param->write.status);
        }
        s_congested = param->write.cong;
        s_write_pending = false;
        notify_tx_task();
        break;
    default:
        break;
    }
}

static esp_err_t init_nvs_for_bluetooth(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS init requires erase, which is not performed by firmware at runtime");
        return err;
    }
    return err;
}

static esp_err_t init_bluetooth(void)
{
    esp_err_t err = init_nvs_for_bluetooth();
    if (err != ESP_OK) {
        return err;
    }

    err = esp_bt_controller_mem_release(ESP_BT_MODE_BLE);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }

    esp_bt_controller_config_t bt_cfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    err = esp_bt_controller_init(&bt_cfg);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }

    err = esp_bt_controller_enable(ESP_BT_MODE_CLASSIC_BT);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }

    err = esp_bluedroid_init();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }

    err = esp_bluedroid_enable();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }

    err = esp_bt_gap_register_callback(gap_callback);
    if (err != ESP_OK) {
        return err;
    }

    err = esp_spp_register_callback(spp_callback);
    if (err != ESP_OK) {
        return err;
    }

    const esp_spp_cfg_t spp_config = {
        .mode = ESP_SPP_MODE_CB,
        .enable_l2cap_ertm = true,
        .tx_buffer_size = 0U,
    };
    return esp_spp_enhanced_init(&spp_config);
}

static bool dequeue_tx_item(v0_spp_tx_item_t *item, TickType_t timeout)
{
    return s_tx_queue != NULL &&
           item != NULL &&
           xQueueReceive(s_tx_queue, item, timeout) == pdTRUE;
}

static void tx_task(void *arg)
{
    (void)arg;

    v0_spp_tx_item_t item = {0};
    for (;;) {
        if (!s_connected || s_congested || s_write_pending) {
            (void)ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(200));
            continue;
        }

        if (!dequeue_tx_item(&item, pdMS_TO_TICKS(200))) {
            continue;
        }

        if (!s_connected || s_congested) {
            add_disconnected_drop();
            continue;
        }

        s_write_pending = true;
        const esp_err_t err = esp_spp_write(s_client_handle, item.length, item.data);
        if (err != ESP_OK) {
            add_write_error();
            s_write_pending = false;
            ESP_LOGW(TAG, "esp_spp_write failed: %s", esp_err_to_name(err));
            continue;
        }

        while (s_connected && s_write_pending) {
            (void)ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(1000));
        }
    }
}

esp_err_t v0_spp_transport_start(void)
{
    if (s_tx_queue == NULL) {
        s_tx_queue = xQueueCreate(BOARD_SPP_TX_QUEUE_DEPTH, sizeof(v0_spp_tx_item_t));
        if (s_tx_queue == NULL) {
            return ESP_ERR_NO_MEM;
        }
    }

    if (s_tx_task_handle == NULL) {
        if (xTaskCreate(tx_task,
                        "v0_spp_tx",
                        BOARD_SPP_TX_TASK_STACK_BYTES,
                        NULL,
                        BOARD_SPP_TX_TASK_PRIORITY,
                        &s_tx_task_handle) != pdPASS) {
            return ESP_ERR_NO_MEM;
        }
    }

    const esp_err_t err = init_bluetooth();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Bluetooth SPP init failed: %s", esp_err_to_name(err));
        return err;
    }

    ESP_LOGI(TAG, "Bluetooth Classic SPP acceptor starting as %s", BOARD_SPP_DEVICE_NAME);
    return ESP_OK;
}

bool v0_spp_transport_enqueue(const uint8_t *data, size_t length)
{
    if (data == NULL || length == 0U || length > PROTOCOL_V0_MAX_PACKET_SIZE || s_tx_queue == NULL) {
        return false;
    }

    if (!s_connected) {
        add_disconnected_drop();
        return false;
    }

    v0_spp_tx_item_t item = {
        .length = (uint16_t)length,
    };
    memcpy(item.data, data, length);

    if (xQueueSend(s_tx_queue, &item, 0U) == pdTRUE) {
        notify_tx_task();
        return true;
    }

    v0_spp_tx_item_t dropped = {0};
    (void)xQueueReceive(s_tx_queue, &dropped, 0U);
    add_queue_overflow();
    if (xQueueSend(s_tx_queue, &item, 0U) == pdTRUE) {
        notify_tx_task();
        return true;
    }

    add_disconnected_drop();
    return false;
}

void v0_spp_transport_get_status(v0_spp_transport_status_t *out_status)
{
    if (out_status == NULL) {
        return;
    }

    out_status->connected = s_connected;
    out_status->congested = s_congested;
    const UBaseType_t queued = s_tx_queue == NULL ? 0U : uxQueueMessagesWaiting(s_tx_queue);
    out_status->queue_usage_percent =
        (uint8_t)((queued * 100U) / BOARD_SPP_TX_QUEUE_DEPTH);

    portENTER_CRITICAL(&s_status_mux);
    out_status->queue_overflow_count = s_queue_overflow_count;
    out_status->disconnected_drop_count = s_disconnected_drop_count;
    out_status->write_error_count = s_write_error_count;
    portEXIT_CRITICAL(&s_status_mux);
}
