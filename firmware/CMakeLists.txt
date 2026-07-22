# The following lines of boilerplate have to be in your project's
# CMakeLists in this exact order for cmake to work correctly
cmake_minimum_required(VERSION 3.22)

set(PROJECT_VER "0.1.0")

# The repository wrapper always passes IDF_TARGET. Keep the historical
# classic ESP32 default for direct CMake invocations until the C3 hardware
# acceptance gates are complete.
if(NOT DEFINED IDF_TARGET)
    set(IDF_TARGET "esp32")
endif()

if(IDF_TARGET STREQUAL "esp32c3")
    list(APPEND EXTRA_COMPONENT_DIRS
        "$ENV{IDF_PATH}/examples/bluetooth/common/ble_uart"
    )
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
# "Trim" the build. Include the minimal set of components, main, and anything it depends on.
idf_build_set_property(MINIMAL_BUILD ON)
project(smart_neckband)
