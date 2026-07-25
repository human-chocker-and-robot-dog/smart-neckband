idf_component_register(
    SRCS "mic_runtime.c"
    REQUIRES neckband_interfaces esp-sr
    PRIV_REQUIRES esp_driver_i2s esp_timer
)
