set(transport_source "spp_transport.c")
set(transport_requirements bt)

if(IDF_TARGET STREQUAL "esp32c3")
    set(transport_source "ble_transport.c")
    set(transport_requirements ble_uart)
elseif(NOT IDF_TARGET STREQUAL "esp32")
    message(FATAL_ERROR "Unsupported smart-neckband target: ${IDF_TARGET}")
endif()

idf_component_register(
    SRCS
        "app_main.c"
        "i2c_scan.c"
        "oled_status.c"
        "packet_task.c"
        "protocol_v0.c"
        "sample_ring.c"
        "sensors.c"
        "${transport_source}"
    PRIV_REQUIRES
        ${transport_requirements}
        esp_adc
        esp_driver_gptimer
        esp_driver_gpio
        esp_driver_i2c
        esp_hw_support
        esp_timer
        nvs_flash
        esp_partition
        spi_flash
    INCLUDE_DIRS
        "."
)
