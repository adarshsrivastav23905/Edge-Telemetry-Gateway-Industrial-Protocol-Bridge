#pragma once
// ===========================================================================
//  Poller — Drift-Free Periodic Modbus Register Polling (C++)
// ===========================================================================
//  Runs on a dedicated std::thread. Uses steady_clock with absolute
//  wake-up times to maintain a precise 500 ms polling cadence.
//
//  Why not sleep(500ms)?
//    If poll + decode takes 20 ms, sleep(500ms) yields a 520 ms cycle
//    that drifts. Absolute wake-ups:
//      next_wake = start + N * interval
//    ensure the long-term average stays exact.
// ===========================================================================

#ifndef POLLER_HPP
#define POLLER_HPP

#include <atomic>
#include <chrono>
#include <string>
#include <thread>
#include <vector>

#include "Config.hpp"
#include "Decoder.hpp"
#include "ModbusClient.hpp"
#include "ThreadSafeQueue.hpp"

class Poller {
public:
    Poller(const DeviceConfig& config,
           const ReconnectConfig& reconnect_cfg,
           ThreadSafeQueue<TelemetryReading>& queue,
           std::atomic<bool>& running);

    ~Poller();

    /// Start the polling thread.
    void start();

    /// Signal the thread to stop and join it.
    void stop();

    // Counters
    uint64_t poll_count()  const { return poll_count_; }
    uint64_t error_count() const { return error_count_; }
    const std::string& device_id() const { return config_.device_id; }

private:
    void run();
    void poll_all_registers();

    DeviceConfig                        config_;
    ReconnectConfig                     reconnect_cfg_;
    ThreadSafeQueue<TelemetryReading>&  queue_;
    std::atomic<bool>&                  running_;

    ModbusClient                        modbus_;
    std::thread                         thread_;

    uint64_t                            poll_count_;
    uint64_t                            error_count_;
};

#endif // POLLER_HPP
