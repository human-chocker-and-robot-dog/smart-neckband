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
#include "host/ble_hs.h"
#include "nvs_flash.h"
#include "protocol_v0.h"

static const char *TAG = "v0_ble";

typedef struct {
    uint16_t length;
    uint8_t data[PROTOCOL_V0_MAX_PACKET_SIZE];
} v0_ble_tx_item_t;

static QueueHandle_t s_tx_queue = NULL;
static TaskHandle_t s_tx_task_handle = NULL;

static volatile bool s_connected = false;
static volatile bool s_congested = false;

static portMUX_TYPE s_status_mux = portMUX_INITIALIZER_UNLOCKED;
static uint32_t s_queue_overflow_count = 0U;
static uint32_t s_disconnected_drop_count = 0U;
static uint32_t s_write_error_count = 0U;

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
    if (queued == 0U) {
        return;
    }

    (void)xQueueReset(s_tx_queue);
    add_disconnected_drops((uint32_t)queued);
    ESP_LOGI(TAG, "dropped %u stale BLE TX packet(s) on %s", (unsigned)queued, reason);
}

static void update_connection_state(void)
{
    const bool connected = ble_uart_is_connected();
    if (connected == s_connected) {
        return;
    }

    if (!connected) {
        drop_pending_tx_queue("disconnect");
        s_congested = false;
        ESP_LOGI(TAG, "BLE client disconnected");
    } else {
        drop_pending_tx_queue("connect");
        ESP_LOGI(TAG, "BLE client connected; notifications=%s",
                 ble_uart_is_subscribed() ? "subscribed" : "pending");
    }
    s_connected = connected;
}

static void tx_task(void *arg)
{
    (void)arg;

    v0_ble_tx_item_t item = {0};
    for (;;) {
        update_connection_state();
        if (!s_connected) {
            vTaskDelay(pdMS_TO_TICKS(50U));
            continue;
        }

        if (xQueueReceive(s_tx_queue, &item, pdMS_TO_TICKS(50U)) != pdTRUE) {
            continue;
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
    (void)data;
    if (length > 0U) {
        ESP_LOGI(TAG, "ignored %u-byte BLE control write (not defined in V0)",
                 (unsigned)length);
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
    if (data == NULL || length == 0U || length > PROTOCOL_V0_MAX_PACKET_SIZE || s_tx_queue == NULL) {
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

void v0_transport_get_status(v0_transport_status_t *out_status)
{
    if (out_status == NULL) {
        return;
    }

    update_connection_state();
    out_status->connected = s_connected;
    out_status->congested = s_congested;
    const UBaseType_t queued = s_tx_queue == NULL ? 0U : uxQueueMessagesWaiting(s_tx_queue);
    out_status->queue_usage_percent =
        (uint8_t)((queued * 100U) / BOARD_TRANSPORT_TX_QUEUE_DEPTH);

    portENTER_CRITICAL(&s_status_mux);
    out_status->queue_overflow_count = s_queue_overflow_count;
    out_status->disconnected_drop_count = s_disconnected_drop_count;
    out_status->write_error_count = s_write_error_count;
    portEXIT_CRITICAL(&s_status_mux);
}
