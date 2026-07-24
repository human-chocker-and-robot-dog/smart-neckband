set(transport_source "spp_transport.c")
set(transport_requirements bt)
set(voice_sources "")

if(IDF_TARGET STREQUAL "esp32c3")
    set(transport_source "ble_transport.c")
    set(transport_requirements ble_uart)
elseif(NOT IDF_TARGET STREQUAL "esp32")
    message(FATAL_ERROR "Unsupported smart-neckband target: ${IDF_TARGET}")
endif()

set(main_sources
    "app_main.c"
    "i2c_scan.c"
    "oled_status.c"
    "packet_task.c"
    "protocol_v0.c"
    "sample_ring.c"
    "sensors.c"
    ${voice_sources}
    "${transport_source}"
)

set(main_requirements
    ${transport_requirements}
    neckband_interfaces
    esp_adc
    esp_driver_gptimer
    esp_driver_gpio
    esp_driver_i2c
    esp_driver_i2s
    esp_hw_support
    esp_timer
    nvs_flash
    esp_partition
    esp_event
    esp_netif
    esp_wifi
    spi_flash
)

idf_component_register(
    SRCS
        ${main_sources}
    PRIV_REQUIRES
        ${main_requirements}
        voice_deps
    INCLUDE_DIRS
        "."
)

if(SMART_NECKBAND_VOICE)
    if(NOT IDF_TARGET STREQUAL "esp32c3")
        message(FATAL_ERROR "SMART_NECKBAND_VOICE is supported only on esp32c3")
    endif()
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SMART_NECKBAND_VOICE=1)
else()
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SMART_NECKBAND_VOICE=0)
endif()
