#pragma once
// ===========================================================================
//  MqttPublisher — Async MQTT Dispatch with Reconnect (C++)
// ===========================================================================
//  Drains the dispatch queue on a dedicated thread, evaluates alerts,
//  serializes readings to JSON, and publishes to the MQTT broker.
//
//  Uses Paho MQTT C++ async_client for non-blocking I/O.
// ===========================================================================

#ifndef MQTT_PUBLISHER_HPP
#define MQTT_PUBLISHER_HPP

#include <atomic>
#include <string>
#include <thread>

#include "AlertEngine.hpp"
#include "Config.hpp"
#include "Decoder.hpp"
#include "ThreadSafeQueue.hpp"

class MqttPublisher {
public:
    MqttPublisher(ThreadSafeQueue<TelemetryReading>& queue,
                  AlertEngine& alert_engine,
                  const MqttConfig& mqtt_cfg,
                  const ReconnectConfig& reconnect_cfg,
                  std::atomic<bool>& running);

    ~MqttPublisher();

    /// Connect and start the dispatch thread.
    void start();

    /// Stop dispatching, drain queue, disconnect.
    void stop();

    // Counters
    uint64_t publish_count() const { return publish_count_; }
    uint64_t alert_count()   const { return alert_count_;   }
    uint64_t drop_count()    const { return drop_count_;    }

private:
    void mqtt_connect();
    void dispatch_loop();
    void publish_data(const TelemetryReading& reading);
    void publish_alert(const AlertEvent& alert);
    std::string reading_to_json(const TelemetryReading& reading);
    std::string alert_to_json(const AlertEvent& alert);

    ThreadSafeQueue<TelemetryReading>&  queue_;
    AlertEngine&                        alert_engine_;
    MqttConfig                          mqtt_cfg_;
    ReconnectConfig                     reconnect_cfg_;
    std::atomic<bool>&                  running_;

    // Paho MQTT client is held as an opaque pointer to avoid
    // exposing the Paho headers in this header file
    void*                               client_;  // mqtt::async_client*
    std::atomic<bool>                   connected_;
    std::thread                         thread_;

    uint64_t                            publish_count_;
    uint64_t                            alert_count_;
    uint64_t                            drop_count_;
};

#endif // MQTT_PUBLISHER_HPP
