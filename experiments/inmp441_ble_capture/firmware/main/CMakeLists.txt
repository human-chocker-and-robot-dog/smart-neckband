idf_component_register(
    SRCS "mic_capture_main.c"
    PRIV_REQUIRES
        ble_uart
        esp_driver_i2s
        esp_hw_support
        nvs_flash
    INCLUDE_DIRS "."
)
