// ===========================================================================
//  Poller.cpp — Drift-Free Polling Implementation
// ===========================================================================

#include "Poller.hpp"

#include <iostream>

Poller::Poller(const DeviceConfig& config,
               const ReconnectConfig& reconnect_cfg,
               ThreadSafeQueue<TelemetryReading>& queue,
               std::atomic<bool>& running)
    : config_(config)
    , reconnect_cfg_(reconnect_cfg)
    , queue_(queue)
    , running_(running)
    , modbus_(config.host, config.port, config.unit_id,
              config.timeout_ms, config.retries,
              reconnect_cfg.initial_delay_ms, reconnect_cfg.max_delay_ms,
              reconnect_cfg.jitter_ms)
    , poll_count_(0)
    , error_count_(0)
{
}

Poller::~Poller() {
    stop();
}

void Poller::start() {
    thread_ = std::thread(&Poller::run, this);
    std::cout << "[Poller:" << config_.device_id << "] Started (interval="
              << config_.poll_interval_ms << "ms)" << std::endl;
}

void Poller::stop() {
    if (thread_.joinable()) {
        thread_.join();
    }
    modbus_.disconnect();
    std::cout << "[Poller:" << config_.device_id << "] Stopped — polls="
              << poll_count_ << " errors=" << error_count_
              << " reconnects=" << modbus_.reconnect_count() << std::endl;
}

void Poller::run() {
    // Initial connection with retry
    while (running_.load()) {
        if (modbus_.connect()) break;
        modbus_.do_backoff();
    }

    // Drift-free polling loop using absolute wake-up times
    auto interval = std::chrono::milliseconds(config_.poll_interval_ms);
    auto next_wake = std::chrono::steady_clock::now();

    while (running_.load()) {
        next_wake += interval;

        if (modbus_.is_connected()) {
            poll_all_registers();
        } else {
            // Attempt reconnection
            if (!modbus_.connect()) {
                modbus_.do_backoff();
            }
        }

        // Sleep until next scheduled wake-up (drift-free)
        auto now = std::chrono::steady_clock::now();
        if (next_wake > now) {
            std::this_thread::sleep_until(next_wake);
        }
        // If we overran, next_wake is already in the past and we
        // immediately proceed to the next poll (catch-up)
    }
}

void Poller::poll_all_registers() {
    for (const auto& reg_def : config_.register_map) {
        if (!running_.load()) return;

        std::vector<uint16_t> raw_values;
        if (!modbus_.read_holding_registers(reg_def.address, reg_def.count,
                                            raw_values)) {
            error_count_++;
            // Connection lost — skip remaining registers this cycle
            return;
        }

        // Decode and push to queue
        TelemetryReading reading = decode_registers(
            config_.device_id, reg_def, raw_values);
        queue_.push(std::move(reading));
    }

    poll_count_++;
    if (poll_count_ % 10 == 0) {
        std::cout << "[Poller:" << config_.device_id << "] Poll #"
                  << poll_count_ << " complete (queue="
                  << queue_.size() << ")" << std::endl;
    }
}
