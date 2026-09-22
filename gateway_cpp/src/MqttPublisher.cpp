// ===========================================================================
//  MqttPublisher.cpp — MQTT Dispatch Implementation
// ===========================================================================

#include "MqttPublisher.hpp"

#include <chrono>
#include <iostream>
#include <random>
#include <sstream>
#include <thread>

#include <mqtt/async_client.h>
#include <nlohmann/json.hpp>

using njson = nlohmann::json;

// ---------------------------------------------------------------------------
// Callback class for Paho MQTT C++ async client
// ---------------------------------------------------------------------------
class MqttCallback : public virtual mqtt::callback {
public:
    explicit MqttCallback(std::atomic<bool>& connected_flag)
        : connected_(connected_flag) {}

    void connected(const std::string& cause) override {
        connected_.store(true);
        std::cout << "[MQTT] Connected: " << cause << std::endl;
    }

    void connection_lost(const std::string& cause) override {
        connected_.store(false);
        std::cout << "[MQTT] Connection lost";
        if (!cause.empty()) std::cout << ": " << cause;
        std::cout << std::endl;
    }

    void delivery_complete(mqtt::delivery_token_ptr tok) override {
        // Optional: track in-flight messages
    }

private:
    std::atomic<bool>& connected_;
};

// ---------------------------------------------------------------------------
// Constructor / Destructor
// ---------------------------------------------------------------------------
MqttPublisher::MqttPublisher(ThreadSafeQueue<TelemetryReading>& queue,
                             AlertEngine& alert_engine,
                             const MqttConfig& mqtt_cfg,
                             const ReconnectConfig& reconnect_cfg,
                             std::atomic<bool>& running)
    : queue_(queue)
    , alert_engine_(alert_engine)
    , mqtt_cfg_(mqtt_cfg)
    , reconnect_cfg_(reconnect_cfg)
    , running_(running)
    , client_(nullptr)
    , connected_(false)
    , publish_count_(0)
    , alert_count_(0)
    , drop_count_(0)
{
}

MqttPublisher::~MqttPublisher() {
    stop();
}

// ---------------------------------------------------------------------------
// Start / Stop
// ---------------------------------------------------------------------------
void MqttPublisher::start() {
    mqtt_connect();
    thread_ = std::thread(&MqttPublisher::dispatch_loop, this);
    std::cout << "[MqttPublisher] Dispatcher started" << std::endl;
}

void MqttPublisher::stop() {
    if (thread_.joinable()) {
        queue_.notify_all();  // wake the dispatcher
        thread_.join();
    }

    // Drain remaining items
    auto remaining = queue_.drain();
    for (const auto& reading : remaining) {
        publish_data(reading);
    }
    std::cout << "[MqttPublisher] Drained " << remaining.size()
              << " readings" << std::endl;

    // Disconnect
    if (client_) {
        auto* cli = static_cast<mqtt::async_client*>(client_);
        try {
            // Publish offline status
            std::string status_topic = mqtt_cfg_.base_topic + "/"
                                     + mqtt_cfg_.client_id + "/status";
            cli->publish(status_topic, "offline", 1, true)->wait();
            cli->disconnect()->wait();
        } catch (...) {}
        delete cli;
        client_ = nullptr;
    }

    std::cout << "[MqttPublisher] Stopped — published=" << publish_count_
              << " alerts=" << alert_count_
              << " dropped=" << drop_count_ << std::endl;
}

// ---------------------------------------------------------------------------
// MQTT connection with backoff
// ---------------------------------------------------------------------------
void MqttPublisher::mqtt_connect() {
    std::string server = "tcp://" + mqtt_cfg_.broker_host + ":"
                       + std::to_string(mqtt_cfg_.broker_port);

    auto* cli = new mqtt::async_client(server, mqtt_cfg_.client_id);
    client_ = cli;

    // Set callback
    static MqttCallback cb(connected_);
    cli->set_callback(cb);

    // Connection options
    mqtt::connect_options connOpts;
    connOpts.set_keep_alive_interval(mqtt_cfg_.keepalive_s);
    connOpts.set_clean_session(true);
    connOpts.set_automatic_reconnect(true);

    // Last Will and Testament
    std::string lwt_topic = mqtt_cfg_.lwt_topic;
    if (lwt_topic.empty()) {
        lwt_topic = mqtt_cfg_.base_topic + "/" + mqtt_cfg_.client_id + "/status";
    }
    mqtt::will_options will(lwt_topic, mqtt_cfg_.lwt_payload,
                            mqtt_cfg_.lwt_qos, mqtt_cfg_.lwt_retain);
    connOpts.set_will(will);

    // Connect with backoff
    int attempt = 0;
    static std::mt19937 rng(std::chrono::steady_clock::now().time_since_epoch().count());

    while (running_.load()) {
        try {
            std::cout << "[MQTT] Connecting to " << server << "..." << std::endl;
            cli->connect(connOpts)->wait();
            connected_.store(true);

            // Publish online status
            std::string status_topic = mqtt_cfg_.base_topic + "/"
                                     + mqtt_cfg_.client_id + "/status";
            cli->publish(status_topic, "online", 1, true)->wait();

            std::cout << "[MQTT] Connected to " << server << std::endl;
            return;
        } catch (const mqtt::exception& e) {
            std::cerr << "[MQTT] Connect error: " << e.what() << std::endl;
        }

        // Backoff
        int delay_ms = reconnect_cfg_.initial_delay_ms;
        for (int i = 0; i < attempt && delay_ms < reconnect_cfg_.max_delay_ms; ++i) {
            delay_ms *= 2;
        }
        std::uniform_int_distribution<int> jitter_dist(0, reconnect_cfg_.jitter_ms);
        delay_ms += jitter_dist(rng);
        if (delay_ms > reconnect_cfg_.max_delay_ms) delay_ms = reconnect_cfg_.max_delay_ms;

        attempt++;
        std::cout << "[MQTT] Backoff: " << delay_ms << " ms (attempt "
                  << attempt << ")" << std::endl;
        std::this_thread::sleep_for(std::chrono::milliseconds(delay_ms));
    }
}

// ---------------------------------------------------------------------------
// Dispatch loop
// ---------------------------------------------------------------------------
void MqttPublisher::dispatch_loop() {
    while (running_.load()) {
        auto item = queue_.pop(std::chrono::milliseconds(500));
        if (!item.has_value()) continue;

        TelemetryReading& reading = item.value();

        // Evaluate alerts
        auto alert = alert_engine_.evaluate(
            reading.device_id, reading.tag, reading.value,
            reading.unit, reading.timestamp);

        if (alert.has_value()) {
            publish_alert(alert.value());
        }

        // Publish data with CoV gating
        if (alert_engine_.has_changed(reading.tag, reading.value)) {
            publish_data(reading);
            alert_engine_.mark_published(reading.tag, reading.value);
        }
    }
}

// ---------------------------------------------------------------------------
// Publishing
// ---------------------------------------------------------------------------
void MqttPublisher::publish_data(const TelemetryReading& reading) {
    if (!connected_.load() || !client_) {
        drop_count_++;
        return;
    }

    auto* cli = static_cast<mqtt::async_client*>(client_);
    std::string topic = mqtt_cfg_.base_topic + "/" + reading.device_id + "/data";
    std::string payload = reading_to_json(reading);

    try {
        cli->publish(topic, payload, mqtt_cfg_.data_qos, false);
        publish_count_++;
    } catch (const mqtt::exception& e) {
        std::cerr << "[MQTT] Publish error: " << e.what() << std::endl;
        drop_count_++;
    }
}

void MqttPublisher::publish_alert(const AlertEvent& alert) {
    if (!connected_.load() || !client_) {
        return;
    }

    auto* cli = static_cast<mqtt::async_client*>(client_);
    std::string topic = mqtt_cfg_.base_topic + "/" + alert.device_id + "/alert";
    std::string payload = alert_to_json(alert);

    try {
        cli->publish(topic, payload, mqtt_cfg_.alert_qos, false)->wait();
        alert_count_++;
        std::cout << "[MQTT] ALERT published: " << topic
                  << " → " << alert.message << std::endl;
    } catch (const mqtt::exception& e) {
        std::cerr << "[MQTT] Alert publish error: " << e.what() << std::endl;
    }
}

// ---------------------------------------------------------------------------
// JSON serialization
// ---------------------------------------------------------------------------
std::string MqttPublisher::reading_to_json(const TelemetryReading& reading) {
    njson j;
    j["device_id"]      = reading.device_id;
    j["tag"]            = reading.tag;
    j["value"]          = reading.value;
    j["unit"]           = reading.unit;
    j["timestamp"]      = reading.timestamp;
    j["raw_registers"]  = reading.raw_registers;
    return j.dump();
}

std::string MqttPublisher::alert_to_json(const AlertEvent& alert) {
    njson j;
    j["device_id"]  = alert.device_id;
    j["tag"]        = alert.tag;
    j["level"]      = alert_level_str(alert.level);
    j["value"]      = alert.value;
    j["threshold"]  = alert.threshold;
    j["unit"]       = alert.unit;
    j["message"]    = alert.message;
    j["timestamp"]  = alert.timestamp;
    return j.dump();
}
