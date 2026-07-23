string(REPLACE "\\" "/" IDF_PATH_CMAKE "$ENV{IDF_PATH}")
set(BLE_UART_UPSTREAM_DIR "${IDF_PATH_CMAKE}/examples/bluetooth/common/ble_uart")

idf_component_register(
    SRCS "ble_uart_nimble_just_works.c"
    INCLUDE_DIRS "${BLE_UART_UPSTREAM_DIR}"
    REQUIRES bt nvs_flash
)
