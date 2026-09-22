// ===========================================================================
//  ModbusClient.cpp — libmodbus TCP Client Implementation
// ===========================================================================

#include "ModbusClient.hpp"

#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <random>
#include <thread>

#include <modbus.h>

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
static std::mt19937& rng() {
    static thread_local std::mt19937 gen(
        std::chrono::steady_clock::now().time_since_epoch().count());
    return gen;
}

static int random_jitter(int max_jitter) {
    if (max_jitter <= 0) return 0;
    std::uniform_int_distribution<int> dist(0, max_jitter);
    return dist(rng());
}

// ---------------------------------------------------------------------------
// Constructor / Destructor
// ---------------------------------------------------------------------------
ModbusClient::ModbusClient(const std::string& host, int port, int unit_id,
                           int timeout_ms, int retries,
                           int initial_backoff_ms, int max_backoff_ms,
                           int jitter_ms)
    : host_(host)
    , port_(port)
    , unit_id_(unit_id)
    , timeout_ms_(timeout_ms)
    , retries_(retries)
    , initial_backoff_ms_(initial_backoff_ms)
    , max_backoff_ms_(max_backoff_ms)
    , jitter_ms_(jitter_ms)
    , ctx_(nullptr)
    , state_(ModbusState::DISCONNECTED)
    , backoff_attempt_(0)
    , reconnect_count_(0)
    , read_error_count_(0)
{
}

ModbusClient::~ModbusClient() {
    disconnect();
}

// ---------------------------------------------------------------------------
// Connection management
// ---------------------------------------------------------------------------
bool ModbusClient::connect() {
    state_ = ModbusState::CONNECTING;
    std::cout << "[ModbusClient] Connecting to " << host_ << ":" << port_
              << " (unit " << unit_id_ << ")..." << std::endl;

    // Clean up any existing context
    if (ctx_) {
        modbus_close(ctx_);
        modbus_free(ctx_);
        ctx_ = nullptr;
    }

    // Create new TCP context
    ctx_ = modbus_new_tcp(host_.c_str(), port_);
    if (ctx_ == nullptr) {
        std::cerr << "[ModbusClient] Failed to create context: "
                  << modbus_strerror(errno) << std::endl;
        state_ = ModbusState::BACKOFF;
        return false;
    }

    // Set slave/unit ID
    if (modbus_set_slave(ctx_, unit_id_) == -1) {
        std::cerr << "[ModbusClient] Failed to set slave ID: "
                  << modbus_strerror(errno) << std::endl;
    }

    // Set response timeout
    modbus_set_response_timeout(ctx_, timeout_ms_ / 1000,
                                (timeout_ms_ % 1000) * 1000);

    // Attempt connection
    if (modbus_connect(ctx_) == -1) {
        std::cerr << "[ModbusClient] Connection failed: "
                  << modbus_strerror(errno) << std::endl;
        modbus_free(ctx_);
        ctx_ = nullptr;
        state_ = ModbusState::BACKOFF;
        return false;
    }

    state_ = ModbusState::CONNECTED;
    backoff_attempt_ = 0;
    reconnect_count_++;
    std::cout << "[ModbusClient] Connected to " << host_ << ":" << port_ << std::endl;
    return true;
}

void ModbusClient::disconnect() {
    if (ctx_) {
        modbus_close(ctx_);
        modbus_free(ctx_);
        ctx_ = nullptr;
    }
    state_ = ModbusState::DISCONNECTED;
}

// ---------------------------------------------------------------------------
// Register reading
// ---------------------------------------------------------------------------
bool ModbusClient::read_holding_registers(uint16_t address, uint16_t count,
                                          std::vector<uint16_t>& dest) {
    if (!ctx_ || state_ != ModbusState::CONNECTED) {
        return false;
    }

    dest.resize(count);

    // modbus_read_registers reads holding registers (FC 0x03)
    int rc = modbus_read_registers(ctx_, address, count, dest.data());
    if (rc == -1) {
        read_error_count_++;
        std::cerr << "[ModbusClient] Read error at addr " << address
                  << ": " << modbus_strerror(errno) << std::endl;

        // Connection may be broken — mark for reconnect
        disconnect();
        state_ = ModbusState::BACKOFF;
        return false;
    }

    if (rc != count) {
        read_error_count_++;
        std::cerr << "[ModbusClient] Short read at addr " << address
                  << ": expected " << count << ", got " << rc << std::endl;
        return false;
    }

    return true;
}

// ---------------------------------------------------------------------------
// Backoff
// ---------------------------------------------------------------------------
int ModbusClient::do_backoff() {
    state_ = ModbusState::BACKOFF;

    // delay = min(initial * 2^attempt + jitter, max)
    int delay_ms = initial_backoff_ms_;
    for (int i = 0; i < backoff_attempt_ && delay_ms < max_backoff_ms_; ++i) {
        delay_ms *= 2;
    }
    delay_ms += random_jitter(jitter_ms_);
    if (delay_ms > max_backoff_ms_) delay_ms = max_backoff_ms_;

    backoff_attempt_++;
    std::cout << "[ModbusClient] Backoff: " << delay_ms << " ms (attempt "
              << backoff_attempt_ << ")" << std::endl;

    std::this_thread::sleep_for(std::chrono::milliseconds(delay_ms));
    return delay_ms;
}
