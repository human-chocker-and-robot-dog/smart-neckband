cmake_minimum_required(VERSION 3.22)

set(IDF_TARGET "esp32c3")
set(PROJECT_VER "0.1.0")

list(APPEND EXTRA_COMPONENT_DIRS
    "${CMAKE_CURRENT_LIST_DIR}/../../../firmware/components/ble_uart"
)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
idf_build_set_property(MINIMAL_BUILD ON)
project(inmp441_ble_capture)
