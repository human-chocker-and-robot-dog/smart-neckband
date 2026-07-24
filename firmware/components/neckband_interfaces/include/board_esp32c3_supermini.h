#pragma once

#define BOARD_NAME "AI Smart Collar V0 / ESP32-C3 SuperMini"
#define BOARD_EXPECTED_IDF_TARGET "esp32c3"
#define BOARD_TRANSPORT_NAME "BLE"
#define BOARD_WIRELESS_DEVICE_NAME "CollarC3"

/*
 * Provisional SuperMini mapping. It deliberately avoids the ESP32-C3
 * strapping pins GPIO2/GPIO8/GPIO9, ADC2 on GPIO5, and native USB on
 * GPIO18/GPIO19. Confirm the exact clone before wiring or flashing.
 */
#define BOARD_ECG_ADC_GPIO GPIO_NUM_0
#define BOARD_ECG_ADC_CHANNEL ADC_CHANNEL_0
#define BOARD_ECG_ADC1_CHANNEL 0U
#define BOARD_ECG_ADC_RAW_MAX 4095
#define BOARD_AD8232_LO_MINUS_GPIO GPIO_NUM_3
#define BOARD_AD8232_LO_PLUS_GPIO GPIO_NUM_10

#define BOARD_I2C_SDA_GPIO GPIO_NUM_6
#define BOARD_I2C_SCL_GPIO GPIO_NUM_7

/*
 * Provisional INMP441 wiring for the Voice build. GPIO5 is used only as a
 * digital I2S word-select signal, so the ADC2/Wi-Fi analog restriction does
 * not apply. Confirm that GPIO20 is exposed by the exact SuperMini clone
 * before wiring or flashing.
 */
#define BOARD_INMP441_BCLK_GPIO GPIO_NUM_4
#define BOARD_INMP441_WS_GPIO GPIO_NUM_5
#define BOARD_INMP441_SD_GPIO GPIO_NUM_20
#define BOARD_INMP441_SAMPLE_RATE_HZ 16000U
#define BOARD_INMP441_PCM_SHIFT 14

#define BOARD_C3_GPIO_IS_RESERVED(gpio) \
    ((gpio) == GPIO_NUM_2 || (gpio) == GPIO_NUM_5 || \
     (gpio) == GPIO_NUM_8 || (gpio) == GPIO_NUM_9 || \
     (gpio) == GPIO_NUM_18 || (gpio) == GPIO_NUM_19)

_Static_assert(BOARD_ECG_ADC_GPIO >= GPIO_NUM_0 &&
               BOARD_ECG_ADC_GPIO <= GPIO_NUM_4 &&
               !BOARD_C3_GPIO_IS_RESERVED(BOARD_ECG_ADC_GPIO),
               "ESP32-C3 ECG input must use an unreserved ADC1 GPIO");
_Static_assert(!BOARD_C3_GPIO_IS_RESERVED(BOARD_AD8232_LO_MINUS_GPIO),
               "ESP32-C3 LO- must avoid strapping, ADC2, and USB GPIOs");
_Static_assert(!BOARD_C3_GPIO_IS_RESERVED(BOARD_AD8232_LO_PLUS_GPIO),
               "ESP32-C3 LO+ must avoid strapping, ADC2, and USB GPIOs");
_Static_assert(!BOARD_C3_GPIO_IS_RESERVED(BOARD_I2C_SDA_GPIO),
               "ESP32-C3 SDA must avoid strapping, ADC2, and USB GPIOs");
_Static_assert(!BOARD_C3_GPIO_IS_RESERVED(BOARD_I2C_SCL_GPIO),
               "ESP32-C3 SCL must avoid strapping, ADC2, and USB GPIOs");
_Static_assert(BOARD_ECG_ADC_GPIO != BOARD_AD8232_LO_MINUS_GPIO &&
               BOARD_ECG_ADC_GPIO != BOARD_AD8232_LO_PLUS_GPIO &&
               BOARD_ECG_ADC_GPIO != BOARD_I2C_SDA_GPIO &&
               BOARD_ECG_ADC_GPIO != BOARD_I2C_SCL_GPIO &&
               BOARD_AD8232_LO_MINUS_GPIO != BOARD_AD8232_LO_PLUS_GPIO &&
               BOARD_I2C_SDA_GPIO != BOARD_I2C_SCL_GPIO,
               "ESP32-C3 board signals must use distinct GPIOs");

_Static_assert(BOARD_INMP441_BCLK_GPIO != BOARD_ECG_ADC_GPIO &&
               BOARD_INMP441_BCLK_GPIO != BOARD_AD8232_LO_MINUS_GPIO &&
               BOARD_INMP441_BCLK_GPIO != BOARD_AD8232_LO_PLUS_GPIO &&
               BOARD_INMP441_BCLK_GPIO != BOARD_I2C_SDA_GPIO &&
               BOARD_INMP441_BCLK_GPIO != BOARD_I2C_SCL_GPIO &&
               BOARD_INMP441_WS_GPIO != BOARD_ECG_ADC_GPIO &&
               BOARD_INMP441_WS_GPIO != BOARD_AD8232_LO_MINUS_GPIO &&
               BOARD_INMP441_WS_GPIO != BOARD_AD8232_LO_PLUS_GPIO &&
               BOARD_INMP441_WS_GPIO != BOARD_I2C_SDA_GPIO &&
               BOARD_INMP441_WS_GPIO != BOARD_I2C_SCL_GPIO &&
               BOARD_INMP441_SD_GPIO != BOARD_ECG_ADC_GPIO &&
               BOARD_INMP441_SD_GPIO != BOARD_AD8232_LO_MINUS_GPIO &&
               BOARD_INMP441_SD_GPIO != BOARD_AD8232_LO_PLUS_GPIO &&
               BOARD_INMP441_SD_GPIO != BOARD_I2C_SDA_GPIO &&
               BOARD_INMP441_SD_GPIO != BOARD_I2C_SCL_GPIO,
               "INMP441 signals must not overlap existing C3 sensors");
