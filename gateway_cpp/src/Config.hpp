#pragma once
// ===========================================================================
//  Config — Gateway Configuration Loader
// ===========================================================================
//  Loads gateway_config.yaml (as JSON fallback using nlohmann/json) and
//  provides typed structs for device, register map, threshold, MQTT, and
//  reconnect settings.
//
//  Note: We use nlohmann/json to parse the config file rather than adding
//  a yaml-cpp dependency. The YAML config is simple enough that a JSON
//  version works identically, and we ship a JSON config for C++.
// ===========================================================================

#ifndef CONFIG_HPP
#define CONFIG_HPP

#include <cstdint>
#include <string>
#include <vector>

/// Definition of one register (measurement point) in the register map.
struct RegisterDef {
    std::string tag;
    uint16_t    address;
    uint16_t    count;        // 1 for 16-bit, 2 for 32-bit float
    std::string data_type;    // "uint16", "int16", "float32"
    std::string word_order;   // "AB" or "BA"
    double      scale;
    double      offset;
    std::string unit;
};

/// Threshold definition for one tag.
struct ThresholdDef {
    std::string tag;
    double      warning;
    double      critical;
    double      hysteresis;
    int         debounce_count;
};

/// Configuration for one Modbus device.
struct DeviceConfig {
    std::string device_id;
    std::string host;
    int         port;
    int         unit_id;
    int         poll_interval_ms;
    int         timeout_ms;
    int         retries;
    std::vector<RegisterDef> register_map;
};

/// MQTT broker settings.
struct MqttConfig {
    std::string broker_host;
    int         broker_port;
    std::string client_id;
    int         keepalive_s;
    std::string base_topic;
    int         data_qos;
    int         alert_qos;
    std::string lwt_topic;
    std::string lwt_payload;
    int         lwt_qos;
    bool        lwt_retain;
};

/// Reconnect backoff parameters.
struct ReconnectConfig {
    int initial_delay_ms;
    int max_delay_ms;
    int jitter_ms;
};

/// Queue settings.
struct QueueConfig {
    int         max_size;
    std::string overflow_policy;
};

/// Top-level gateway configuration.
struct GatewayConfig {
    std::vector<DeviceConfig>    devices;
    std::vector<ThresholdDef>    thresholds;
    MqttConfig                   mqtt;
    ReconnectConfig              reconnect;
    QueueConfig                  queue;
    std::string                  log_level;
};

/// Load configuration from a JSON file.
/// Throws std::runtime_error on parse failure.
GatewayConfig load_config(const std::string& config_path);

#endif // CONFIG_HPP
