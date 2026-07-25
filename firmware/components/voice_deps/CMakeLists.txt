set(voice_sources "")
set(voice_managed_requirements "")

if(SMART_NECKBAND_VOICE)
    if(NOT IDF_TARGET STREQUAL "esp32c3")
        message(FATAL_ERROR "SMART_NECKBAND_VOICE is supported only on esp32c3")
    endif()
    list(APPEND voice_sources
        "volc_asr_protocol.c"
        "voice_audio.c"
        "voice_config.c"
        "voice_link.c"
        "voice_runtime.c"
        "voice_wakenet.c"
    )
endif()

if(SMART_NECKBAND_VOICE)
    list(APPEND voice_managed_requirements
        cjson
        esp-sr
        esp_websocket_client
    )
endif()

idf_component_register(
    SRCS
        ${voice_sources}
    INCLUDE_DIRS
        "."
    REQUIRES
        neckband_interfaces
        ${voice_managed_requirements}
    PRIV_REQUIRES
        ble_uart
        esp_adc
        esp_driver_gpio
        esp_driver_i2c
        esp_driver_i2s
        esp_event
        esp_netif
        esp_partition
        esp_timer
        esp_wifi
        nvs_flash
)
