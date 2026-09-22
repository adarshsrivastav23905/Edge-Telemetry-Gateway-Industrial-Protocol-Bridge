// ===========================================================================
//  Config.cpp — JSON Configuration Loader Implementation
// ===========================================================================

#include "Config.hpp"
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

GatewayConfig load_config(const std::string& config_path) {
    // Read file
    std::ifstream file(config_path);
    if (!file.is_open()) {
        throw std::runtime_error("Cannot open config file: " + config_path);
    }

    json j;
    try {
        file >> j;
    } catch (const json::parse_error& e) {
        throw std::runtime_error("JSON parse error in " + config_path + ": " + e.what());
    }

    GatewayConfig cfg;

    // --- Devices ---
    for (const auto& dev : j.at("devices")) {
        DeviceConfig dc;
        dc.device_id       = dev.at("id").get<std::string>();
        dc.host            = dev.value("host", "localhost");
        dc.port            = dev.value("port", 5020);
        dc.unit_id         = dev.value("unit_id", 1);
        dc.poll_interval_ms= dev.value("poll_interval_ms", 500);
        dc.timeout_ms      = dev.value("timeout_ms", 1000);
        dc.retries         = dev.value("retries", 3);

        // Load register map for this device
        const std::string& dev_id = dc.device_id;
        if (j.contains("register_maps") && j["register_maps"].contains(dev_id)) {
            for (const auto& reg : j["register_maps"][dev_id]) {
                RegisterDef rd;
                rd.tag        = reg.at("tag").get<std::string>();
                rd.address    = reg.at("address").get<uint16_t>();
                rd.count      = reg.value("count", 1);
                rd.data_type  = reg.value("data_type", "uint16");
                rd.word_order = reg.value("word_order", "AB");
                rd.scale      = reg.value("scale", 1.0);
                rd.offset     = reg.value("offset", 0.0);
                rd.unit       = reg.value("unit", "");
                dc.register_map.push_back(rd);
            }
        }

        cfg.devices.push_back(dc);
    }

    // --- Thresholds ---
    if (j.contains("thresholds")) {
        for (auto it = j["thresholds"].begin(); it != j["thresholds"].end(); ++it) {
            ThresholdDef td;
            td.tag            = it.key();
            td.warning        = it.value().value("warning", 0.0);
            td.critical       = it.value().value("critical", 0.0);
            td.hysteresis     = it.value().value("hysteresis", 0.0);
            td.debounce_count = it.value().value("debounce_count", 1);
            cfg.thresholds.push_back(td);
        }
    }

    // --- MQTT ---
    if (j.contains("mqtt")) {
        const auto& m       = j["mqtt"];
        cfg.mqtt.broker_host = m.value("broker_host", "localhost");
        cfg.mqtt.broker_port = m.value("broker_port", 1883);
        cfg.mqtt.client_id   = m.value("client_id", "edge_gateway_01");
        cfg.mqtt.keepalive_s = m.value("keepalive_s", 60);
        cfg.mqtt.base_topic  = m.value("base_topic", "telemetry");
        cfg.mqtt.data_qos    = m.value("data_qos", 0);
        cfg.mqtt.alert_qos   = m.value("alert_qos", 1);
        cfg.mqtt.lwt_topic   = m.value("lwt_topic", "");
        cfg.mqtt.lwt_payload = m.value("lwt_payload", "offline");
        cfg.mqtt.lwt_qos     = m.value("lwt_qos", 1);
        cfg.mqtt.lwt_retain  = m.value("lwt_retain", true);
    }

    // --- Reconnect ---
    if (j.contains("reconnect")) {
        const auto& r             = j["reconnect"];
        cfg.reconnect.initial_delay_ms = r.value("initial_delay_ms", 1000);
        cfg.reconnect.max_delay_ms     = r.value("max_delay_ms", 60000);
        cfg.reconnect.jitter_ms        = r.value("jitter_ms", 500);
    } else {
        cfg.reconnect = {1000, 60000, 500};
    }

    // --- Queue ---
    if (j.contains("queue")) {
        const auto& q          = j["queue"];
        cfg.queue.max_size       = q.value("max_size", 1000);
        cfg.queue.overflow_policy= q.value("overflow_policy", "drop_oldest");
    } else {
        cfg.queue = {1000, "drop_oldest"};
    }

    // --- Logging ---
    cfg.log_level = j.value("log_level", "INFO");

    std::cout << "[Config] Loaded " << cfg.devices.size() << " device(s), "
              << cfg.thresholds.size() << " threshold(s)" << std::endl;

    return cfg;
}
