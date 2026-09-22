// ===========================================================================
//  main.cpp — Edge Telemetry Gateway Entry Point (C++)
// ===========================================================================
//  1. Loads JSON configuration
//  2. Creates the bounded dispatch queue
//  3. Initialises the alert engine
//  4. Starts the MQTT publisher (dispatcher thread)
//  5. Starts per-device Modbus pollers (poller threads)
//  6. Handles SIGINT/SIGTERM for graceful shutdown:
//       - Sets atomic running flag to false
//       - Wakes condition variables
//       - Joins all threads
//       - Drains the queue
//       - Prints final statistics
//
//  Build:
//    cd gateway_cpp && mkdir build && cd build
//    cmake .. && make
//    ./edge_gateway ../config/gateway_config.json
// ===========================================================================

#include <atomic>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <string>
#include <vector>

#include "AlertEngine.hpp"
#include "Config.hpp"
#include "Decoder.hpp"
#include "ModbusClient.hpp"
#include "MqttPublisher.hpp"
#include "Poller.hpp"
#include "ThreadSafeQueue.hpp"

// ---------------------------------------------------------------------------
// Global shutdown flag
// ---------------------------------------------------------------------------
static std::atomic<bool> g_running{true};

static void signal_handler(int signum) {
    std::cout << "\n[Gateway] Received signal " << signum
              << " — initiating graceful shutdown..." << std::endl;
    g_running.store(false);
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
int main(int argc, char* argv[]) {
    // --- Parse arguments ---
    std::string config_path = "../config/gateway_config.json";
    if (argc > 1) {
        config_path = argv[1];
    }

    std::cout << "=================================================================\n"
              << "  Edge Telemetry Gateway — Modbus TCP to MQTT Bridge (C++)\n"
              << "================================================================="
              << std::endl;

    // --- Load configuration ---
    GatewayConfig config;
    try {
        config = load_config(config_path);
    } catch (const std::exception& e) {
        std::cerr << "[FATAL] " << e.what() << std::endl;
        return EXIT_FAILURE;
    }

    // --- Register signal handlers ---
    std::signal(SIGINT,  signal_handler);
    std::signal(SIGTERM, signal_handler);

    // --- Create dispatch queue ---
    auto dispatch_queue = std::make_unique<ThreadSafeQueue<TelemetryReading>>(
        static_cast<size_t>(config.queue.max_size));
    std::cout << "[Gateway] Dispatch queue: max_size=" << config.queue.max_size
              << " policy=" << config.queue.overflow_policy << std::endl;

    // --- Create alert engine ---
    AlertEngine alert_engine(config.thresholds);

    // --- Create MQTT publisher ---
    auto publisher = std::make_unique<MqttPublisher>(
        *dispatch_queue, alert_engine, config.mqtt, config.reconnect, g_running);
    publisher->start();

    // --- Create pollers (one per device) ---
    std::vector<std::unique_ptr<Poller>> pollers;
    for (const auto& dev_cfg : config.devices) {
        auto poller = std::make_unique<Poller>(
            dev_cfg, config.reconnect, *dispatch_queue, g_running);
        poller->start();
        pollers.push_back(std::move(poller));
    }

    std::cout << "[Gateway] Running — " << pollers.size()
              << " device(s) being polled. Press Ctrl+C to stop." << std::endl;

    // --- Wait for shutdown ---
    while (g_running.load()) {
        std::this_thread::sleep_for(std::chrono::seconds(1));
    }

    // --- Graceful shutdown ---
    std::cout << "\n[Gateway] Shutting down..." << std::endl;

    // Stop pollers first (stop producing)
    for (auto& poller : pollers) {
        poller->stop();
    }

    // Then stop publisher (finish consuming + drain)
    publisher->stop();

    // --- Final statistics ---
    std::cout << "=================================================================\n"
              << "  FINAL STATISTICS\n"
              << "================================================================="
              << std::endl;
    for (const auto& poller : pollers) {
        std::cout << "  [" << poller->device_id() << "] polls="
                  << poller->poll_count() << " errors="
                  << poller->error_count() << std::endl;
    }
    std::cout << "  Publisher: published=" << publisher->publish_count()
              << " alerts=" << publisher->alert_count()
              << " dropped=" << publisher->drop_count() << std::endl;
    std::cout << "  Queue: enqueued=" << dispatch_queue->enqueued_count()
              << " dequeued=" << dispatch_queue->dequeued_count()
              << " dropped=" << dispatch_queue->dropped_count() << std::endl;
    std::cout << "=================================================================\n"
              << "  Gateway shutdown complete.\n"
              << "================================================================="
              << std::endl;

    return EXIT_SUCCESS;
}
