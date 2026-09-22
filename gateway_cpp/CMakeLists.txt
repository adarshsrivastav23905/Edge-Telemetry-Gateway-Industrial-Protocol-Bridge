# ============================================================================
# CMakeLists.txt — Edge Telemetry Gateway (C++)
# ============================================================================
# Build:
#   cd gateway_cpp && mkdir build && cd build
#   cmake .. && make
#
# Dependencies (Ubuntu/Debian):
#   sudo apt install libmodbus-dev libpaho-mqttpp-dev libpaho-mqtt-dev \
#                    nlohmann-json3-dev cmake g++
# ============================================================================

cmake_minimum_required(VERSION 3.14)
project(EdgeTelemetryGateway
    VERSION 1.0.0
    LANGUAGES CXX
    DESCRIPTION "Modbus TCP to MQTT Industrial Protocol Bridge"
)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# --- Find dependencies ---

# libmodbus (C library, via pkg-config)
find_package(PkgConfig REQUIRED)
pkg_check_modules(LIBMODBUS REQUIRED libmodbus)

# Paho MQTT C++ (includes C library transitively)
find_package(PahoMqttCpp REQUIRED)

# nlohmann/json (header-only)
find_package(nlohmann_json 3.2.0 REQUIRED)

# --- Source files ---
set(SOURCES
    src/main.cpp
    src/Config.cpp
    src/ModbusClient.cpp
    src/Decoder.cpp
    src/Poller.cpp
    src/AlertEngine.cpp
    src/MqttPublisher.cpp
)

set(HEADERS
    src/Config.hpp
    src/ModbusClient.hpp
    src/Decoder.hpp
    src/Poller.hpp
    src/AlertEngine.hpp
    src/MqttPublisher.hpp
    src/ThreadSafeQueue.hpp
)

# --- Build target ---
add_executable(edge_gateway ${SOURCES} ${HEADERS})

target_include_directories(edge_gateway PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    ${LIBMODBUS_INCLUDE_DIRS}
)

target_link_libraries(edge_gateway PRIVATE
    ${LIBMODBUS_LIBRARIES}
    PahoMqttCpp::paho-mqttpp3
    nlohmann_json::nlohmann_json
    pthread
)

target_compile_options(edge_gateway PRIVATE
    -Wall -Wextra -Wpedantic
    ${LIBMODBUS_CFLAGS_OTHER}
)

# --- Install ---
install(TARGETS edge_gateway DESTINATION bin)
