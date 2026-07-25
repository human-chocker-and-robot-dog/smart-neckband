if(NOT IDF_TARGET STREQUAL "esp32c3")
    message(FATAL_ERROR "Smart Neckband supports only IDF_TARGET=esp32c3")
endif()

set(main_sources
    "app_main.c"
    "i2c_scan.c"
    "oled_status.c"
    "packet_task.c"
    "protocol_v0.c"
    "sample_ring.c"
    "sensors.c"
    "ble_transport.c"
)

set(main_requirements
    ble_uart
    neckband_interfaces
    esp_adc
    esp_driver_gptimer
    esp_driver_gpio
    esp_driver_i2c
    esp_hw_support
    esp_timer
    nvs_flash
    esp_partition
    spi_flash
)

idf_component_register(
    SRCS
        ${main_sources}
    PRIV_REQUIRES
        ${main_requirements}
    INCLUDE_DIRS
        "."
)

if(SMART_NECKBAND_MIC)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SMART_NECKBAND_MIC=1)
else()
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SMART_NECKBAND_MIC=0)
endif()
