#include "transport.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "ble_uart.h"
#include "board_config.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "host/ble_gap.h"
#include "host/ble_hs.h"
#if SMART_NECKBAND_MIC
#include "mic_runtime.h"
#endif
#include "nvs_flash.h"
#include "protocol_v0.h"

static const char *TAG = "v0_ble";

typedef struct {
    uint16_t length;
    uint8_t data[BOARD_TRANSPORT_MAX_FRAME_SIZE];
} v0_ble_tx_item_t;

static QueueHandle_t s_tx_queue = NULL;
static QueueHandle_t s_low_priority_tx_queue = NULL;
static TaskHandle_t s_tx_task_handle = NULL;
static v0_transport_rx_callback_t s_rx_callback = NULL;

static volatile bool s_connected = false;
static volatile bool s_subscribed = false;
static volatile bool s_congested = false;
static volatile uint16_t s_connection_interval_units = 0U;
static bool s_connection_params_requested = false;

static portMUX_TYPE s_status_mux = portMUX_INITIALIZER_UNLOCKED;
static uint32_t s_queue_overflow_count = 0U;
static uint32_t s_disconnected_drop_count = 0U;
static uint32_t s_write_error_count = 0U;

extern int ble_gap_conn_foreach_handle(
    ble_gap_conn_foreach_handle_fn *callback, void *arg);

static int update_connection_callback(uint16_t conn_handle, void *arg)
{
    struct ble_gap_conn_desc description = {0};
    if (ble_gap_conn_find(conn_handle, &description) != 0) {
        return 0;
    }
    s_connection_interval_units = description.conn_itvl;
    if (*(bool *)arg && !s_connection_params_requested) {
        const struct ble_gap_upd_params parameters = {
            .itvl_min = 6U,
            .itvl_max = 12U,
            .latency = 0U,
            .supervision_timeout = 400U,
            .min_ce_len = 0U,
            .max_ce_len = 0U,
        };
        const int result = ble_gap_update_params(conn_handle, &parameters);
        if (result == 0 || result == BLE_HS_EALREADY) {
            s_connection_params_requested = true;
        } else {
            ESP_LOGW(TAG, "connection parameter update failed rc=%d", result);
        }
    }
    return 0;
}

static void refresh_connection_parameters(bool request_update)
{
    (void)ble_gap_conn_foreach_handle(update_connection_callback, &request_update);
}

static void add_queue_overflow(void)
{
    portENTER_CRITICAL(&s_status_mux);
    ++s_queue_overflow_count;
    portEXIT_CRITICAL(&s_status_mux);
}

static void add_disconnected_drops(uint32_t count)
{
    if (count == 0U) {
        return;
    }
    portENTER_CRITICAL(&s_status_mux);
    s_disconnected_drop_count += count;
    portEXIT_CRITICAL(&s_status_mux);
}

static void add_write_error(void)
{
    portENTER_CRITICAL(&s_status_mux);
    ++s_write_error_count;
    portEXIT_CRITICAL(&s_status_mux);
}

static void drop_pending_tx_queue(const char *reason)
{
    if (s_tx_queue == NULL) {
        return;
    }

    const UBaseType_t queued = uxQueueMessagesWaiting(s_tx_queue);
    const UBaseType_t low_priority_queued =
        s_low_priority_tx_queue == NULL ? 0U :
        uxQueueMessagesWaiting(s_low_priority_tx_queue);
    if (queued == 0U && low_priority_queued == 0U) {
        return;
    }

    (void)xQueueReset(s_tx_queue);
    if (s_low_priority_tx_queue != NULL) {
        (void)xQueueReset(s_low_priority_tx_queue);
    }
    add_disconnected_drops((uint32_t)(queued + low_priority_queued));
    ESP_LOGI(TAG, "dropped %u stale BLE TX packet(s) on %s",
             (unsigned)(queued + low_priority_queued),
             reason);
}

static void update_connection_state(void)
{
    const bool connected = ble_uart_is_connected();
    const bool subscribed = ble_uart_is_subscribed();
    if (connected == s_connected && subscribed == s_subscribed) {
        if (connected) {
            refresh_connection_parameters(false);
        }
        return;
    }

    if (s_connected && !connected) {
        drop_pending_tx_queue("disconnect");
        s_congested = false;
        s_connection_interval_units = 0U;
        s_connection_params_requested = false;
        ESP_LOGI(TAG, "BLE client disconnected");
    } else if (!s_connected && connected) {
        drop_pending_tx_queue("connect");
        refresh_connection_parameters(true);
        ESP_LOGI(TAG, "BLE client connected; notifications=%s",
                 subscribed ? "subscribed" : "pending");
    } else if (connected && subscribed != s_subscribed) {
        ESP_LOGI(TAG,
                 "BLE notifications %s",
                 subscribed ? "subscribed" : "unsubscribed");
    }
    s_connected = connected;
    s_subscribed = subscribed;
}

static void tx_task(void *arg)
{
    (void)arg;

    v0_ble_tx_item_t item = {0};
    v0_ble_tx_item_t pending_low_priority_item = {0};
    bool has_pending_low_priority_item = false;
    for (;;) {
        update_connection_state();
        if (!s_connected) {
            if (has_pending_low_priority_item) {
                add_disconnected_drops(1U);
                has_pending_low_priority_item = false;
            }
            vTaskDelay(pdMS_TO_TICKS(50U));
            continue;
        }

        bool low_priority = false;
        if (xQueueReceive(s_tx_queue, &item, 0U) != pdTRUE) {
            if (has_pending_low_priority_item) {
                item = pending_low_priority_item;
                has_pending_low_priority_item = false;
            } else if (s_low_priority_tx_queue == NULL ||
                       xQueueReceive(s_low_priority_tx_queue, &item, 0U) != pdTRUE) {
                vTaskDelay(1U);
                continue;
            }
            low_priority = true;
        }

        if (low_priority) {
            v0_ble_tx_item_t high_priority_item = {0};
            if (xQueueReceive(s_tx_queue, &high_priority_item, 0U) == pdTRUE) {
                pending_low_priority_item = item;
                has_pending_low_priority_item = true;
                item = high_priority_item;
            }
        }

        update_connection_state();
        if (!s_connected) {
            add_disconnected_drops(1U);
            continue;
        }

        const int rc = ble_uart_tx(item.data, item.length);
        if (rc == BLE_UART_OK) {
            s_congested = false;
            continue;
        }

        if (rc == BLE_UART_ENOTCONN) {
            add_disconnected_drops(1U);
            s_connected = false;
            s_congested = false;
            continue;
        }

        /* ble_uart_tx may already have emitted part of a packet before an
         * mbuf failure. Retrying the whole packet would duplicate a prefix;
         * drop it and let the V0 magic/CRC/sequence recover on the next one. */
        s_congested = rc == BLE_UART_ENOMEM;
        add_write_error();
        ESP_LOGW(TAG, "BLE notify failed rc=%d length=%u", rc, (unsigned)item.length);
    }
}

static esp_err_t init_nvs_for_ble(void)
{
    const esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS init requires erase; firmware will not erase at runtime");
    }
    return err;
}

static void on_ble_rx(const uint8_t *data, size_t length)
{
    if (data == NULL || length == 0U) {
        return;
    }
    if (length >= 2U &&
        data[0] == (uint8_t)(PROTOCOL_V0_MAGIC & 0xffU) &&
        data[1] == (uint8_t)(PROTOCOL_V0_MAGIC >> 8U)) {
        if (s_rx_callback != NULL) {
            s_rx_callback(data, length);
        }
        return;
    }
#if SMART_NECKBAND_MIC
    if (mic_runtime_handle_command(data, length)) {
        return;
    }
    mic_runtime_record_control_error();
    return;
#endif
    if (s_rx_callback != NULL) {
        s_rx_callback(data, length);
    }
}

static void configure_just_works_bonding(void)
{
    /* ble_uart encrypted mode defaults to DisplayOnly + MITM and prints a
     * random passkey only to the serial log. This board has no guaranteed
     * display during bring-up, so keep Secure Connections, encryption, and
     * bonding while allowing the PC application to complete Just Works.
     * This must run after install() sets the defaults and before open()
     * starts the NimBLE host task. */
    ble_hs_cfg.sm_io_cap = BLE_HS_IO_NO_INPUT_OUTPUT;
    ble_hs_cfg.sm_sc = 1;
    ble_hs_cfg.sm_bonding = 1;
    ble_hs_cfg.sm_mitm = 0;
}

esp_err_t v0_transport_start(void)
{
    if (s_tx_queue == NULL) {
        s_tx_queue = xQueueCreate(BOARD_TRANSPORT_TX_QUEUE_DEPTH, sizeof(v0_ble_tx_item_t));
        if (s_tx_queue == NULL) {
            return ESP_ERR_NO_MEM;
        }
    }
    if (s_low_priority_tx_queue == NULL) {
        s_low_priority_tx_queue = xQueueCreate(
            BOARD_TRANSPORT_LOW_PRIORITY_QUEUE_DEPTH, sizeof(v0_ble_tx_item_t));
        if (s_low_priority_tx_queue == NULL) {
            return ESP_ERR_NO_MEM;
        }
    }

    esp_err_t err = init_nvs_for_ble();
    if (err != ESP_OK) {
        return err;
    }

    uint8_t mac[6] = {0};
    const esp_err_t mac_err = esp_read_mac(mac, ESP_MAC_BT);
    if (mac_err != ESP_OK) {
        ESP_LOGW(TAG, "failed to read BLE MAC: %s", esp_err_to_name(mac_err));
    }

    char device_name[24] = {0};
    (void)snprintf(device_name,
                   sizeof(device_name),
                   "%s-%02X%02X",
                   BOARD_WIRELESS_DEVICE_NAME,
                   mac[4],
                   mac[5]);

    const int install_rc = ble_uart_install(&(ble_uart_config_t){
        .encrypted = true,
        .device_name = device_name,
        .ble_uart_on_rx = on_ble_rx,
    });
    if (install_rc != BLE_UART_OK) {
        ESP_LOGE(TAG, "BLE UART install failed rc=%d", install_rc);
        return ESP_FAIL;
    }

    configure_just_works_bonding();

    const int open_rc = ble_uart_open();
    if (open_rc != BLE_UART_OK) {
        ESP_LOGE(TAG, "BLE UART open failed rc=%d", open_rc);
        return ESP_FAIL;
    }

    if (s_tx_task_handle == NULL &&
        xTaskCreate(tx_task,
                    "v0_ble_tx",
                    BOARD_TRANSPORT_TX_TASK_STACK_BYTES,
                    NULL,
                    BOARD_TRANSPORT_TX_TASK_PRIORITY,
                    &s_tx_task_handle) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(TAG,
             "BLE GATT transport advertising as %s; pairing=SC Just Works + bonding; "
             "V0 bytes use ATT MTU fragmentation",
             device_name);
    return ESP_OK;
}

bool v0_transport_enqueue(const uint8_t *data, size_t length)
{
    if (data == NULL || length == 0U ||
        length > BOARD_TRANSPORT_MAX_FRAME_SIZE || s_tx_queue == NULL) {
        return false;
    }

    update_connection_state();
    if (!s_connected) {
        add_disconnected_drops(1U);
        return false;
    }

    v0_ble_tx_item_t item = {
        .length = (uint16_t)length,
    };
    memcpy(item.data, data, length);

    if (xQueueSend(s_tx_queue, &item, 0U) == pdTRUE) {
        return true;
    }

    v0_ble_tx_item_t dropped = {0};
    (void)xQueueReceive(s_tx_queue, &dropped, 0U);
    add_queue_overflow();
    if (xQueueSend(s_tx_queue, &item, 0U) == pdTRUE) {
        return true;
    }

    add_disconnected_drops(1U);
    return false;
}

bool v0_transport_enqueue_low_priority(const uint8_t *data, size_t length)
{
    if (data == NULL || length == 0U || length > BOARD_TRANSPORT_MAX_FRAME_SIZE ||
        s_low_priority_tx_queue == NULL) {
        return false;
    }

    update_connection_state();
    if (!s_connected) {
        add_disconnected_drops(1U);
        return false;
    }

    v0_ble_tx_item_t item = {
        .length = (uint16_t)length,
    };
    memcpy(item.data, data, length);
    if (xQueueSend(s_low_priority_tx_queue, &item, 0U) == pdTRUE) {
        return true;
    }
    return false;
}

void v0_transport_set_rx_callback(v0_transport_rx_callback_t callback)
{
    s_rx_callback = callback;
}

void v0_transport_get_status(v0_transport_status_t *out_status)
{
    if (out_status == NULL) {
        return;
    }

    update_connection_state();
    out_status->connected = s_connected;
    out_status->subscribed = s_subscribed;
    out_status->congested = s_congested;
    out_status->connection_interval_units = s_connection_interval_units;
    const UBaseType_t queued = s_tx_queue == NULL ? 0U : uxQueueMessagesWaiting(s_tx_queue);
    out_status->queue_usage_percent =
        (uint8_t)((queued * 100U) / BOARD_TRANSPORT_TX_QUEUE_DEPTH);

    portENTER_CRITICAL(&s_status_mux);
    out_status->queue_overflow_count = s_queue_overflow_count;
    out_status->disconnected_drop_count = s_disconnected_drop_count;
    out_status->write_error_count = s_write_error_count;
    portEXIT_CRITICAL(&s_status_mux);
}
