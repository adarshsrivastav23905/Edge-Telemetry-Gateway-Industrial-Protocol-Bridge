#!/usr/bin/env python3
"""
=============================================================================
 Decoder — Binary Register Data → Engineering Units
=============================================================================
 Converts raw 16-bit Modbus holding register values into meaningful
 measurements using a data-driven register map from the YAML config.

 Supported data types:
   uint16  : unsigned 16-bit integer with scale factor and offset
   int16   : signed 16-bit integer (two's complement) with scale and offset
   float32 : IEEE-754 float encoded across two consecutive registers

 Word order for float32:
   AB : big-endian (high word first) — most common in industrial devices
   BA : little-endian (low word first) — some vendors (e.g., Schneider)
=============================================================================
"""

import struct
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("Decoder")


@dataclass
class RegisterDefinition:
    """Describes one measurement point in the register map."""
    tag: str
    address: int
    count: int          # 1 for 16-bit, 2 for 32-bit
    data_type: str      # "uint16", "int16", "float32"
    word_order: str     # "AB" (big-endian) or "BA" (little-endian)
    scale: float
    offset: float
    unit: str


@dataclass
class TelemetryReading:
    """A decoded measurement ready for JSON serialization."""
    device_id: str
    tag: str
    value: float
    unit: str
    timestamp: str       # ISO-8601
    raw_registers: list  # for diagnostics


RegisterDef = RegisterDefinition


def parse_register_map(config: dict, device_id: str) -> list[RegisterDefinition]:
    """
    Parse the register_maps section of the YAML config for a specific device.

    Args:
        config: Parsed YAML configuration dict.
        device_id: The device identifier (e.g., "meter_01").

    Returns:
        List of RegisterDefinition objects.
    """
    reg_defs = []
    raw_map = config.get("register_maps", {}).get(device_id, [])
    for entry in raw_map:
        reg_defs.append(RegisterDefinition(
            tag=entry["tag"],
            address=entry["address"],
            count=entry.get("count", 1),
            data_type=entry.get("data_type", "uint16"),
            word_order=entry.get("word_order", "AB"),
            scale=entry.get("scale", 1.0),
            offset=entry.get("offset", 0.0),
            unit=entry.get("unit", ""),
        ))
    log.info("Loaded %d register definitions for device '%s'", len(reg_defs), device_id)
    return reg_defs


def decode_uint16(registers: list[int], scale: float = 1.0, offset: float = 0.0) -> float:
    """
    Decode a single unsigned 16-bit register.

    Example:
        raw = 1500, scale = 0.01 → 15.00 A
    """
    raw = registers[0] & 0xFFFF
    return raw * scale + offset


def decode_int16(registers: list[int], scale: float = 1.0, offset: float = 0.0) -> float:
    """
    Decode a single signed 16-bit register (two's complement).

    Example:
        raw = 0xFF9C (65436) → signed = -100 → × 0.1 → -10.0 °C
    """
    raw = registers[0] & 0xFFFF
    # Convert unsigned to signed via struct
    signed_val = struct.unpack(">h", struct.pack(">H", raw))[0]
    return signed_val * scale + offset


def decode_float32(registers: list[int], word_order: str = "AB",
                   scale: float = 1.0, offset: float = 0.0) -> float:
    """
    Decode a 32-bit IEEE-754 float from two consecutive 16-bit registers.

    Word order:
        AB: registers[0] = high word, registers[1] = low word (big-endian)
        BA: registers[0] = low word,  registers[1] = high word (little-endian)

    Worked example (word order AB):
        Voltage = 230.5 V
        IEEE-754: 0x43668000
        Register[0] (high) = 0x4366 = 17254
        Register[1] (low)  = 0x8000 = 32768
        Packed bytes: 43 66 80 00
        Unpacked float: 230.5
    """
    if len(registers) < 2:
        log.error("float32 decode requires 2 registers, got %d", len(registers))
        return float("nan")

    hi = registers[0] & 0xFFFF
    lo = registers[1] & 0xFFFF

    if word_order == "BA":
        hi, lo = lo, hi  # swap to big-endian order

    # Pack as two big-endian uint16, then unpack as big-endian float
    packed = struct.pack(">HH", hi, lo)
    value = struct.unpack(">f", packed)[0]

    return value * scale + offset


def decode_registers(
    device_id: str,
    reg_def: RegisterDefinition,
    raw_values: list[int],
) -> TelemetryReading:
    """
    Decode raw register values for a single measurement point.

    Args:
        device_id: Device identifier.
        reg_def: Register definition from the config.
        raw_values: Raw 16-bit register values read from the device.

    Returns:
        TelemetryReading with the decoded engineering value.
    """
    if reg_def.data_type == "uint16":
        value = decode_uint16(raw_values, reg_def.scale, reg_def.offset)
    elif reg_def.data_type == "int16":
        value = decode_int16(raw_values, reg_def.scale, reg_def.offset)
    elif reg_def.data_type == "float32":
        value = decode_float32(raw_values, reg_def.word_order,
                               reg_def.scale, reg_def.offset)
    else:
        log.error("Unknown data type '%s' for tag '%s'", reg_def.data_type, reg_def.tag)
        value = float("nan")

    return TelemetryReading(
        device_id=device_id,
        tag=reg_def.tag,
        value=round(value, 4),
        unit=reg_def.unit,
        timestamp=datetime.now(timezone.utc).isoformat(),
        raw_registers=list(raw_values),
    )
