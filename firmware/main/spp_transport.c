#include "spp_transport.h"

#include <stdbool.h>
#include <inttypes.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
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
static TaskHandle_t s_discovery_task_handle = NULL;

static volatile bool s_connected = false;
static volatile bool s_congested = false;
static volatile bool s_write_pending = false;
static uint32_t s_client_handle = 0U;

static portMUX_TYPE s_status_mux = portMUX_INITIALIZER_UNLOCKED;
static uint32_t s_queue_overflow_count = 0U;
static uint32_t s_disconnected_drop_count = 0U;
static uint32_t s_write_error_count = 0U;

static char *bda_to_string(const uint8_t *bda, char *buffer, size_t buffer_size)
{
    if (bda == NULL || buffer == NULL || buffer_size < 18U) {
        return NULL;
    }
    snprintf(buffer,
             buffer_size,
             "%02x:%02x:%02x:%02x:%02x:%02x",
             bda[0],
             bda[1],
             bda[2],
             bda[3],
             bda[4],
             bda[5]);
    return buffer;
}

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

static void log_gap_profile_status(const char *label)
{
    esp_bt_gap_profile_status_t status = {0};
    const esp_err_t err = esp_bt_gap_get_profile_status(&status);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "BT GAP profile status unavailable after %s: %s", label, esp_err_to_name(err));
        return;
    }

    ESP_LOGI(TAG,
             "BT GAP profile after %s: conn_mode=%d disc_mode=%d disc_state=%d",
             label,
             status.conn_mode,
             status.disc_mode,
             status.disc_stat);
}

static bool gap_is_discoverable(void)
{
    esp_bt_gap_profile_status_t status = {0};
    return esp_bt_gap_get_profile_status(&status) == ESP_OK &&
           status.conn_mode == ESP_BT_CONNECTABLE &&
           status.disc_mode == ESP_BT_GENERAL_DISCOVERABLE;
}

static void request_scan_mode(const char *label)
{
    const esp_err_t err = esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "set BT scan mode failed after %s: %s", label, esp_err_to_name(err));
        return;
    }

    ESP_LOGI(TAG, "BT scan mode requested after %s", label);
    log_gap_profile_status(label);
}

static void discovery_watchdog_task(void *arg)
{
    (void)arg;

    for (uint8_t attempt = 1U; attempt <= 12U; ++attempt) {
        vTaskDelay(pdMS_TO_TICKS(1000U));
        if (gap_is_discoverable()) {
            log_gap_profile_status("watchdog_ok");
            break;
        }

        request_scan_mode("watchdog");

        esp_bt_cod_t actual_cod = {0};
        if (esp_bt_gap_get_cod(&actual_cod) == ESP_OK) {
            ESP_LOGI(TAG,
                     "BT COD watchdog major=%" PRIu32 " minor=%" PRIu32 " service=0x%03" PRIx32,
                     actual_cod.major,
                     actual_cod.minor,
                     actual_cod.service);
        }
    }

    s_discovery_task_handle = NULL;
    vTaskDelete(NULL);
}

static void start_discovery_watchdog(void)
{
    if (s_discovery_task_handle != NULL) {
        return;
    }

    if (xTaskCreate(discovery_watchdog_task,
                    "v0_bt_disc",
                    4096U,
                    NULL,
                    BOARD_SPP_TX_TASK_PRIORITY,
                    &s_discovery_task_handle) != pdPASS) {
        ESP_LOGW(TAG, "failed to start BT discovery watchdog");
    }
}

static void configure_discovery_identity(void)
{
    esp_bt_eir_data_t eir = {
        .fec_required = true,
        .include_txpower = true,
        .include_uuid = false,
        .include_name = true,
        .flag = ESP_BT_EIR_FLAG_GEN_DISC,
        .manufacturer_len = 0U,
        .p_manufacturer_data = NULL,
        .url_len = 0U,
        .p_url = NULL,
    };
    esp_err_t err = esp_bt_gap_config_eir_data(&eir);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "config BT EIR failed: %s", esp_err_to_name(err));
    }

    const esp_bt_cod_t cod = {
        .reserved_2 = 0U,
        .minor = 0U,
        .major = ESP_BT_COD_MAJOR_DEV_COMPUTER,
        .service = ESP_BT_COD_SRVC_INFORMATION | ESP_BT_COD_SRVC_OBJ_TRANSFER,
        .reserved_8 = 0U,
    };
    err = esp_bt_gap_set_cod(cod, ESP_BT_INIT_COD);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "set BT class-of-device failed: %s", esp_err_to_name(err));
        return;
    }

    esp_bt_cod_t actual_cod = {0};
    err = esp_bt_gap_get_cod(&actual_cod);
    if (err == ESP_OK) {
        ESP_LOGI(TAG,
                 "BT class-of-device major=%" PRIu32 " minor=%" PRIu32 " service=0x%03" PRIx32,
                 actual_cod.major,
                 actual_cod.minor,
                 actual_cod.service);
    }
}

static void set_discoverable_mode(void)
{
    esp_err_t err = esp_bt_gap_set_device_name(BOARD_SPP_DEVICE_NAME);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "set BT device name failed: %s", esp_err_to_name(err));
        return;
    }

    configure_discovery_identity();

    request_scan_mode("spp_start");
    ESP_LOGI(TAG, "BT discoverable/connectable as %s", BOARD_SPP_DEVICE_NAME);
    start_discovery_watchdog();
}

static void configure_pairing(void)
{
    esp_bt_sp_param_t param_type = ESP_BT_SP_IOCAP_MODE;
    esp_bt_io_cap_t iocap = ESP_BT_IO_CAP_NONE;
    esp_err_t err = esp_bt_gap_set_security_param(param_type, &iocap, sizeof(iocap));
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "set BT SSP IO capability failed: %s", esp_err_to_name(err));
    } else {
        ESP_LOGI(TAG, "BT pairing IO capability set to no-input/no-output");
    }

    esp_bt_pin_type_t pin_type = ESP_BT_PIN_TYPE_VARIABLE;
    esp_bt_pin_code_t pin_code = {0};
    err = esp_bt_gap_set_pin(pin_type, 0U, pin_code);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "set BT legacy PIN mode failed: %s", esp_err_to_name(err));
    }
}

static void gap_callback(esp_bt_gap_cb_event_t event, esp_bt_gap_cb_param_t *param)
{
    char bda[18] = {0};

    switch (event) {
    case ESP_BT_GAP_CONFIG_EIR_DATA_EVT:
        ESP_LOGI(TAG,
                 "BT EIR configured status=%d type_count=%u",
                 param->config_eir_data.stat,
                 (unsigned)param->config_eir_data.eir_type_num);
        if (param->config_eir_data.stat == ESP_BT_STATUS_SUCCESS) {
            request_scan_mode("eir_configured");
        }
        break;
    case ESP_BT_GAP_AUTH_CMPL_EVT:
        if (param->auth_cmpl.stat == ESP_BT_STATUS_SUCCESS) {
            ESP_LOGI(TAG,
                     "BT authentication success name=%s bda=%s",
                     param->auth_cmpl.device_name,
                     bda_to_string(param->auth_cmpl.bda, bda, sizeof(bda)));
        } else {
            ESP_LOGW(TAG, "BT authentication failed status=%d", param->auth_cmpl.stat);
        }
        break;
    case ESP_BT_GAP_PIN_REQ_EVT: {
        ESP_LOGI(TAG, "BT legacy PIN requested min_16_digit=%d", param->pin_req.min_16_digit);
        esp_bt_pin_code_t pin_code = {0};
        if (param->pin_req.min_16_digit) {
            esp_bt_gap_pin_reply(param->pin_req.bda, true, 16U, pin_code);
        } else {
            pin_code[0] = '1';
            pin_code[1] = '2';
            pin_code[2] = '3';
            pin_code[3] = '4';
            esp_bt_gap_pin_reply(param->pin_req.bda, true, 4U, pin_code);
        }
        break;
    }
    case ESP_BT_GAP_CFM_REQ_EVT:
        ESP_LOGI(TAG, "BT SSP confirm requested value=%06" PRIu32, param->cfm_req.num_val);
        esp_bt_gap_ssp_confirm_reply(param->cfm_req.bda, true);
        break;
    case ESP_BT_GAP_KEY_NOTIF_EVT:
        ESP_LOGI(TAG, "BT SSP passkey notification=%06" PRIu32, param->key_notif.passkey);
        break;
    case ESP_BT_GAP_KEY_REQ_EVT:
        ESP_LOGI(TAG, "BT SSP passkey requested");
        break;
    case ESP_BT_GAP_MODE_CHG_EVT:
        ESP_LOGI(TAG,
                 "BT mode changed mode=%d bda=%s",
                 param->mode_chg.mode,
                 bda_to_string(param->mode_chg.bda, bda, sizeof(bda)));
        break;
    default:
        break;
    }
}

static void spp_callback(esp_spp_cb_event_t event, esp_spp_cb_param_t *param)
{
    char bda[18] = {0};

    switch (event) {
    case ESP_SPP_INIT_EVT:
        ESP_LOGI(TAG, "SPP init status=%d", param->init.status);
        if (param->init.status == ESP_SPP_SUCCESS) {
            esp_err_t err = esp_spp_start_srv(ESP_SPP_SEC_AUTHENTICATE,
                                              ESP_SPP_ROLE_SLAVE,
                                              0U,
                                              BOARD_SPP_SERVER_NAME);
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "SPP start server failed: %s", esp_err_to_name(err));
            }
        }
        break;
    case ESP_SPP_START_EVT:
        ESP_LOGI(TAG,
                 "SPP server start status=%d handle=%" PRIu32 " scn=%d",
                 param->start.status,
                 param->start.handle,
                 param->start.scn);
        if (param->start.status == ESP_SPP_SUCCESS) {
            set_discoverable_mode();
        }
        break;
    case ESP_SPP_SRV_OPEN_EVT:
        s_client_handle = param->srv_open.handle;
        s_connected = true;
        s_congested = false;
        s_write_pending = false;
        ESP_LOGI(TAG,
                 "SPP client connected status=%d handle=%" PRIu32 " rem_bda=%s",
                 param->srv_open.status,
                 s_client_handle,
                 bda_to_string(param->srv_open.rem_bda, bda, sizeof(bda)));
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

    esp_bluedroid_config_t bluedroid_cfg = BT_BLUEDROID_INIT_CONFIG_DEFAULT();
    err = esp_bluedroid_init_with_cfg(&bluedroid_cfg);
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
    err = esp_spp_enhanced_init(&spp_config);
    if (err != ESP_OK) {
        return err;
    }

    configure_pairing();

    char bda[18] = {0};
    ESP_LOGI(TAG, "BT own address=%s", bda_to_string(esp_bt_dev_get_address(), bda, sizeof(bda)));
    return ESP_OK;
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
