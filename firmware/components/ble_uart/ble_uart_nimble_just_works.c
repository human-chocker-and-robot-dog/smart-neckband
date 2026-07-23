/*
 * Project adapter for ESP-IDF v6.0.2's example BLE UART component.
 *
 * The upstream encrypted profile requires both link encryption and MITM
 * authentication on the RX characteristic and TX CCCD. This project uses a
 * headless LE Secure Connections Just Works profile, which is encrypted and
 * bonded but intentionally reports authenticated=0. Keep every upstream
 * implementation detail while removing only the incompatible AUTHEN flags;
 * the ENC flags remain intact.
 */

#include "sdkconfig.h"

#if CONFIG_BT_NIMBLE_ENABLED
#include "host/ble_gatt.h"

#undef BLE_GATT_CHR_F_READ_AUTHEN
#undef BLE_GATT_CHR_F_WRITE_AUTHEN
#undef BLE_GATT_CHR_F_NOTIFY_INDICATE_AUTHEN
#define BLE_GATT_CHR_F_READ_AUTHEN 0
#define BLE_GATT_CHR_F_WRITE_AUTHEN 0
#define BLE_GATT_CHR_F_NOTIFY_INDICATE_AUTHEN 0
#endif

#include "ble_uart_nimble.c"
