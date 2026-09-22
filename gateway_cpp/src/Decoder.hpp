#pragma once
// ===========================================================================
//  Decoder — Register Binary Data → Engineering Units (C++)
// ===========================================================================
//  Converts raw 16-bit Modbus holding register values into meaningful
//  measurements using the register map from configuration.
//
//  Supported types:
//    uint16  : raw * scale + offset
//    int16   : two's complement signed → raw * scale + offset
//    float32 : IEEE-754 from two registers, word order AB or BA
// ===========================================================================

#ifndef DECODER_HPP
#define DECODER_HPP

#include <chrono>
#include <cstdint>
#include <string>
#include <vector>

#include "Config.hpp"

/// A decoded telemetry reading.
struct TelemetryReading {
    std::string device_id;
    std::string tag;
    double      value;
    std::string unit;
    std::string timestamp;  // ISO-8601
    std::vector<uint16_t> raw_registers;
};

/// Decode a uint16 register with scale and offset.
double decode_uint16(const std::vector<uint16_t>& regs, double scale, double offset);

/// Decode a signed int16 register (two's complement) with scale and offset.
double decode_int16(const std::vector<uint16_t>& regs, double scale, double offset);

/// Decode a 32-bit IEEE-754 float from two 16-bit registers.
/// word_order "AB" = big-endian (high word first), "BA" = low word first.
double decode_float32(const std::vector<uint16_t>& regs,
                      const std::string& word_order,
                      double scale, double offset);

/// Decode registers according to the register definition.
TelemetryReading decode_registers(const std::string& device_id,
                                  const RegisterDef& reg_def,
                                  const std::vector<uint16_t>& raw_values);

/// Get current time as ISO-8601 string.
std::string get_iso_timestamp();

#endif // DECODER_HPP
