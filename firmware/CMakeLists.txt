# The following lines of boilerplate have to be in your project's
# CMakeLists in this exact order for cmake to work correctly
cmake_minimum_required(VERSION 3.22)

set(PROJECT_VER "0.1.0")

# The repository supports only the ESP32-C3 hardware baseline.
if(NOT DEFINED IDF_TARGET)
    set(IDF_TARGET "esp32c3")
endif()

if(NOT IDF_TARGET STREQUAL "esp32c3")
    message(FATAL_ERROR "Smart Neckband supports only IDF_TARGET=esp32c3")
endif()

list(APPEND EXTRA_COMPONENT_DIRS
    "${CMAKE_CURRENT_LIST_DIR}/components/ble_uart"
)

list(APPEND EXTRA_COMPONENT_DIRS
    "${CMAKE_CURRENT_LIST_DIR}/components/voice_deps"
)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
# "Trim" the build. Include the minimal set of components, main, and anything it depends on.
idf_build_set_property(MINIMAL_BUILD ON)
project(smart_neckband)
