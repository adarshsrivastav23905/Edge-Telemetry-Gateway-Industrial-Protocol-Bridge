// ===========================================================================
//  Decoder.cpp — Register Decoding Implementation
// ===========================================================================

#include "Decoder.hpp"

#include <cmath>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <sstream>

// ---------------------------------------------------------------------------
// Timestamp helper
// ---------------------------------------------------------------------------
std::string get_iso_timestamp() {
    auto now = std::chrono::system_clock::now();
    auto time_t_now = std::chrono::system_clock::to_time_t(now);
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        now.time_since_epoch()) % 1000;

    std::tm tm_buf{};
#ifdef _WIN32
    gmtime_s(&tm_buf, &time_t_now);
#else
    gmtime_r(&time_t_now, &tm_buf);
#endif

    std::ostringstream oss;
    oss << std::put_time(&tm_buf, "%Y-%m-%dT%H:%M:%S")
        << '.' << std::setfill('0') << std::setw(3) << ms.count()
        << 'Z';
    return oss.str();
}

// ---------------------------------------------------------------------------
// uint16 decode
// ---------------------------------------------------------------------------
double decode_uint16(const std::vector<uint16_t>& regs, double scale, double offset) {
    if (regs.empty()) return std::nan("");
    uint16_t raw = regs[0];
    return static_cast<double>(raw) * scale + offset;
}

// ---------------------------------------------------------------------------
// int16 decode (two's complement)
// ---------------------------------------------------------------------------
double decode_int16(const std::vector<uint16_t>& regs, double scale, double offset) {
    if (regs.empty()) return std::nan("");
    uint16_t raw = regs[0];
    // Reinterpret as signed
    int16_t signed_val;
    std::memcpy(&signed_val, &raw, sizeof(int16_t));
    return static_cast<double>(signed_val) * scale + offset;
}

// ---------------------------------------------------------------------------
// float32 decode from two 16-bit registers
// ---------------------------------------------------------------------------
//
// Worked example (word order AB, big-endian):
//   Voltage = 230.5 V
//   IEEE-754 representation: 0x43668000
//   Register[0] (high word) = 0x4366 = 17254
//   Register[1] (low word)  = 0x8000 = 32768
//
//   Packed as bytes: 0x43 0x66 0x80 0x00
//   Unpacked: 230.5
//
double decode_float32(const std::vector<uint16_t>& regs,
                      const std::string& word_order,
                      double scale, double offset) {
    if (regs.size() < 2) {
        std::cerr << "[Decoder] float32 requires 2 registers, got "
                  << regs.size() << std::endl;
        return std::nan("");
    }

    uint16_t hi = regs[0];
    uint16_t lo = regs[1];

    if (word_order == "BA") {
        std::swap(hi, lo);
    }

    // Combine into 32-bit integer, then reinterpret as float
    uint32_t combined = (static_cast<uint32_t>(hi) << 16) | static_cast<uint32_t>(lo);
    float result;
    std::memcpy(&result, &combined, sizeof(float));

    return static_cast<double>(result) * scale + offset;
}

// ---------------------------------------------------------------------------
// Main decode dispatcher
// ---------------------------------------------------------------------------
TelemetryReading decode_registers(const std::string& device_id,
                                  const RegisterDef& reg_def,
                                  const std::vector<uint16_t>& raw_values) {
    double value;

    if (reg_def.data_type == "uint16") {
        value = decode_uint16(raw_values, reg_def.scale, reg_def.offset);
    } else if (reg_def.data_type == "int16") {
        value = decode_int16(raw_values, reg_def.scale, reg_def.offset);
    } else if (reg_def.data_type == "float32") {
        value = decode_float32(raw_values, reg_def.word_order,
                               reg_def.scale, reg_def.offset);
    } else {
        std::cerr << "[Decoder] Unknown data_type: " << reg_def.data_type << std::endl;
        value = std::nan("");
    }

    // Round to 4 decimal places
    value = std::round(value * 10000.0) / 10000.0;

    return TelemetryReading{
        device_id,
        reg_def.tag,
        value,
        reg_def.unit,
        get_iso_timestamp(),
        raw_values
    };
}
