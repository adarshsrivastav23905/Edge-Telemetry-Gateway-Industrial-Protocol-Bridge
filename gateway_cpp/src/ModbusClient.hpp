#pragma once
// ===========================================================================
//  ModbusClient — libmodbus Wrapper with Connection State Machine
// ===========================================================================
//  Encapsulates libmodbus calls for Modbus TCP communication.
//  Manages connection lifecycle with exponential backoff on failure.
//
//  State machine:
//    DISCONNECTED → CONNECTING → CONNECTED → (error) → BACKOFF → CONNECTING
// ===========================================================================

#ifndef MODBUS_CLIENT_HPP
#define MODBUS_CLIENT_HPP

#include <cstdint>
#include <string>
#include <vector>

// Forward declare the opaque libmodbus type
struct _modbus;
typedef struct _modbus modbus_t;

enum class ModbusState {
    DISCONNECTED,
    CONNECTING,
    CONNECTED,
    BACKOFF
};

class ModbusClient {
public:
    ModbusClient(const std::string& host, int port, int unit_id,
                 int timeout_ms = 1000, int retries = 3,
                 int initial_backoff_ms = 1000, int max_backoff_ms = 60000,
                 int jitter_ms = 500);

    ~ModbusClient();

    // Non-copyable
    ModbusClient(const ModbusClient&) = delete;
    ModbusClient& operator=(const ModbusClient&) = delete;

    /// Attempt to connect. Returns true on success.
    bool connect();

    /// Disconnect cleanly.
    void disconnect();

    /// Read holding registers. Returns true on success.
    /// On failure, triggers reconnect/backoff.
    bool read_holding_registers(uint16_t address, uint16_t count,
                                std::vector<uint16_t>& dest);

    /// Current connection state.
    ModbusState state() const { return state_; }

    /// Whether we're connected and ready to read.
    bool is_connected() const { return state_ == ModbusState::CONNECTED; }

    /// Perform one backoff wait cycle. Returns the delay used (ms).
    int do_backoff();

    // Counters
    uint64_t reconnect_count() const { return reconnect_count_; }
    uint64_t read_error_count() const { return read_error_count_; }

private:
    std::string host_;
    int         port_;
    int         unit_id_;
    int         timeout_ms_;
    int         retries_;
    int         initial_backoff_ms_;
    int         max_backoff_ms_;
    int         jitter_ms_;

    modbus_t*   ctx_;
    ModbusState state_;
    int         backoff_attempt_;

    uint64_t    reconnect_count_;
    uint64_t    read_error_count_;
};

#endif // MODBUS_CLIENT_HPP
