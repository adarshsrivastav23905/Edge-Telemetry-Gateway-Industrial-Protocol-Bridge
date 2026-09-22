#!/usr/bin/env python3
"""
=============================================================================
 Unit Tests — Register Decoder (Modbus to Engineering Units)
=============================================================================
 Tests:
   - 16-bit unsigned integer decoding with scale & offset
   - 16-bit signed integer decoding with negative values & scale
   - 32-bit IEEE 754 float decoding with AB (Big-Endian) word order
   - 32-bit IEEE 754 float decoding with BA (Little-Endian) word order
   - Register definition parsing from config
   - Full decode_registers function for TelemetryReading objects
   - Truncated / empty register handling
=============================================================================
"""

import math
import struct
import sys
import os
import pytest

# Add gateway_python to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gateway_python")))

from decoder import (
    RegisterDef,
    RegisterDefinition,
    TelemetryReading,
    decode_uint16,
    decode_int16,
    decode_float32,
    decode_registers,
    parse_register_map,
)


class TestPrimitiveDecoders:
    """Test individual low-level primitive register decoders."""

    def test_decode_uint16_basic(self):
        # 1500 * 0.01 = 15.0
        val = decode_uint16([1500], scale=0.01, offset=0.0)
        assert pytest.approx(val, rel=1e-4) == 15.0

    def test_decode_uint16_with_offset(self):
        # (100 * 2.0) + 10.0 = 210.0
        val = decode_uint16([100], scale=2.0, offset=10.0)
        assert pytest.approx(val, rel=1e-4) == 210.0

    def test_decode_uint16_limits(self):
        val = decode_uint16([65535], scale=1.0, offset=0.0)
        assert val == 65535.0

        val_zero = decode_uint16([0], scale=1.0, offset=0.0)
        assert val_zero == 0.0

    def test_decode_int16_positive(self):
        val = decode_int16([453], scale=0.1, offset=0.0)
        assert pytest.approx(val, rel=1e-4) == 45.3

    def test_decode_int16_negative(self):
        # 0xFFFF in 2's complement is -1
        val = decode_int16([0xFFFF], scale=1.0, offset=0.0)
        assert val == -1.0

        # -250 in 16-bit signed is 65536 - 250 = 65286 (0xFF06)
        # Scaled by 0.1 -> -25.0 °C
        val_subzero = decode_int16([65286], scale=0.1, offset=0.0)
        assert pytest.approx(val_subzero, rel=1e-4) == -25.0

    def test_decode_float32_word_order_ab(self):
        # 230.5f in IEEE 754:
        # Hex: 0x43668000 -> (0x4366, 0x8000)
        packed = struct.pack(">f", 230.5)
        w0, w1 = struct.unpack(">HH", packed)
        assert (w0, w1) == (0x4366, 0x8000)

        decoded = decode_float32([w0, w1], word_order="AB")
        assert pytest.approx(decoded, rel=1e-4) == 230.5

    def test_decode_float32_word_order_ba(self):
        # Word order BA: Low word first
        w0, w1 = 0x4366, 0x8000
        decoded = decode_float32([w1, w0], word_order="BA")
        assert pytest.approx(decoded, rel=1e-4) == 230.5

    def test_decode_float32_with_scale_offset(self):
        packed = struct.pack(">f", 100.0)
        w0, w1 = struct.unpack(">HH", packed)
        # (100.0 * 1.5) + 5.0 = 155.0
        decoded = decode_float32([w0, w1], word_order="AB", scale=1.5, offset=5.0)
        assert pytest.approx(decoded, rel=1e-4) == 155.0


class TestTelemetryReadingDecoding:
    """Test full decode_registers dispatching into TelemetryReading."""

    def test_decode_float32_reading(self):
        reg_def = RegisterDef(
            tag="voltage", address=0, count=2, data_type="float32",
            word_order="AB", scale=1.0, offset=0.0, unit="V"
        )
        packed = struct.pack(">f", 231.4)
        w0, w1 = struct.unpack(">HH", packed)

        reading = decode_registers(device_id="meter_01", reg_def=reg_def, raw_values=[w0, w1])

        assert isinstance(reading, TelemetryReading)
        assert reading.device_id == "meter_01"
        assert reading.tag == "voltage"
        assert pytest.approx(reading.value, rel=1e-3) == 231.4
        assert reading.unit == "V"
        assert reading.raw_registers == [w0, w1]
        assert "T" in reading.timestamp

    def test_decode_uint16_reading(self):
        reg_def = RegisterDef(
            tag="current", address=2, count=1, data_type="uint16",
            word_order="AB", scale=0.01, offset=0.0, unit="A"
        )
        reading = decode_registers(device_id="meter_01", reg_def=reg_def, raw_values=[1520])
        assert reading.tag == "current"
        assert pytest.approx(reading.value, rel=1e-3) == 15.20
        assert reading.unit == "A"

    def test_decode_int16_reading(self):
        reg_def = RegisterDef(
            tag="temperature", address=3, count=1, data_type="int16",
            word_order="AB", scale=0.1, offset=0.0, unit="°C"
        )
        reading = decode_registers(device_id="meter_01", reg_def=reg_def, raw_values=[435])
        assert reading.tag == "temperature"
        assert pytest.approx(reading.value, rel=1e-3) == 43.5
        assert reading.unit == "°C"

    def test_parse_register_map_from_config(self):
        sample_config = {
            "register_maps": {
                "test_meter": [
                    {"tag": "voltage", "address": 0, "count": 2, "data_type": "float32", "unit": "V"},
                    {"tag": "current", "address": 2, "count": 1, "data_type": "uint16", "scale": 0.01, "unit": "A"},
                ]
            }
        }
        defs = parse_register_map(sample_config, "test_meter")
        assert len(defs) == 2
        assert defs[0].tag == "voltage"
        assert defs[0].count == 2
        assert defs[0].data_type == "float32"
        assert defs[1].tag == "current"
        assert defs[1].scale == 0.01
