#!/usr/bin/env python3
"""
=============================================================================
 Modbus TCP Slave Simulator — Simulated Industrial Energy Meter / RTU
=============================================================================
 Emulates a field device exposing holding registers over Modbus TCP.

 Register Map (mirrors gateway_config.yaml):
   Address 0-1 : Voltage     (32-bit IEEE-754 float, big-endian word order)
   Address 2   : Current     (uint16, scale 0.01 → amps)
   Address 3   : Temperature (int16,  scale 0.1  → °C)
   Address 4   : Power Factor(uint16, scale 0.001)
   Address 5   : Frequency   (uint16, scale 0.01 → Hz)

 The simulator updates registers every 250 ms with realistic drift using
 bounded random walks. Fault injection modes let you test the gateway's
 resilience:
   --fault spike      : occasional voltage spikes above critical threshold
   --fault freeze     : stop updating registers (stale data)
   --fault disconnect : periodically drop the TCP connection

 Usage:
   python meter_sim.py --port 5020 --unit-id 1
   python meter_sim.py --port 5020 --fault spike
=============================================================================
"""

import argparse
import logging
import math
import os
import random
import signal
import struct
import sys
import threading
import time

from pymodbus.server import StartTcpServer

try:
    from pymodbus.datastore import (
        ModbusSequentialDataBlock,
        ModbusSlaveContext,
        ModbusServerContext,
    )
except ImportError:  # pymodbus >= 3.8 uses ModbusDeviceContext instead of ModbusSlaveContext
    from pymodbus.datastore import (
        ModbusSequentialDataBlock,
        ModbusServerContext,
        ModbusDeviceContext,
    )
    ModbusSlaveContext = ModbusDeviceContext

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)-5s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("MeterSim")

# ---------------------------------------------------------------------------
# Simulated sensor parameters
# ---------------------------------------------------------------------------
SIM_PARAMS = {
    "voltage": {"nominal": 230.0, "drift_pct": 0.02, "min": 200.0, "max": 260.0},
    "current": {"nominal": 15.0, "drift_pct": 0.05, "min": 0.5, "max": 30.0},
    "temperature": {"nominal": 45.0, "drift_pct": 0.03, "min": 10.0, "max": 90.0},
    "power_factor": {"nominal": 0.95, "drift_pct": 0.01, "min": 0.80, "max": 1.00},
    "frequency": {"nominal": 50.0, "drift_pct": 0.002, "min": 49.5, "max": 50.5},
}


class SensorSimulator:
    """Generates realistic drifting sensor values using a bounded random walk."""

    def __init__(self, nominal: float, drift_pct: float, lo: float, hi: float):
        self.nominal = nominal
        self.max_step = nominal * drift_pct
        self.lo = lo
        self.hi = hi
        self.value = nominal + random.uniform(-self.max_step, self.max_step)

    def step(self) -> float:
        """Advance one tick: random walk with mean-reversion and bounds."""
        # Mean-reversion pull (stronger when far from nominal)
        pull = (self.nominal - self.value) * 0.05
        delta = pull + random.gauss(0, self.max_step)
        self.value = max(self.lo, min(self.hi, self.value + delta))
        return self.value


def float_to_registers(value: float) -> tuple[int, int]:
    """
    Encode an IEEE-754 float into two 16-bit Modbus registers.
    Big-endian word order (AB): register[0] = high word, register[1] = low word.
    This matches how most industrial devices expose 32-bit floats.

    Example:
      230.5 → 0x43668000
      high word = 0x4366, low word = 0x8000
      registers = (17254, 32768)
    """
    packed = struct.pack(">f", value)  # big-endian float
    high, low = struct.unpack(">HH", packed)
    return high, low


def int_to_register(value: float, scale: float) -> int:
    """Scale a float to a 16-bit integer register value, clamped to [0, 65535]."""
    raw = int(round(value / scale))
    return max(0, min(65535, raw))


def signed_int_to_register(value: float, scale: float) -> int:
    """Scale a float to a signed 16-bit integer, stored as unsigned in Modbus."""
    raw = int(round(value / scale))
    raw = max(-32768, min(32767, raw))
    if raw < 0:
        raw += 65536  # two's complement for unsigned storage
    return raw


class RegisterUpdater:
    """
    Background thread that updates the Modbus data store with simulated
    sensor readings every update_interval seconds.
    """

    def __init__(
        self,
        context: ModbusServerContext,
        unit_id: int,
        update_interval: float = 0.25,
        fault_mode: str = "none",
    ):
        self.context = context
        self.unit_id = unit_id
        self.interval = update_interval
        self.fault_mode = fault_mode
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="RegUpdater")

        # Create sensor simulators
        self.sensors = {
            name: SensorSimulator(p["nominal"], p["drift_pct"], p["min"], p["max"])
            for name, p in SIM_PARAMS.items()
        }

        # Counters
        self.update_count = 0
        self.spike_count = 0

    def start(self):
        self._thread.start()
        log.info("Register updater started (interval=%.0fms, fault=%s)",
                 self.interval * 1000, self.fault_mode)

    def stop(self):
        self._stop_event.set()
        self._thread.join(timeout=5.0)
        log.info("Register updater stopped after %d updates (%d spikes injected)",
                 self.update_count, self.spike_count)

    def _run(self):
        """Main update loop with drift-free scheduling."""
        next_wake = time.monotonic()
        while not self._stop_event.is_set():
            next_wake += self.interval
            try:
                self._update_registers()
            except Exception:
                log.exception("Error updating registers")
            # Sleep until next scheduled wake-up
            sleep_time = next_wake - time.monotonic()
            if sleep_time > 0:
                self._stop_event.wait(timeout=sleep_time)

    def _update_registers(self):
        """Step all sensors and write values into the Modbus data store."""
        # Step each sensor
        voltage = self.sensors["voltage"].step()
        current = self.sensors["current"].step()
        temperature = self.sensors["temperature"].step()
        pf = self.sensors["power_factor"].step()
        freq = self.sensors["frequency"].step()

        # --- Fault injection ---
        if self.fault_mode == "spike" and random.random() < 0.02:
            # 2% chance of a voltage spike above critical (253 V)
            voltage = random.uniform(255.0, 270.0)
            self.spike_count += 1
            log.warning("FAULT INJECT: voltage spike → %.1f V", voltage)

        if self.fault_mode == "freeze":
            # Don't update — registers keep their last values
            return

        # Encode to register values
        v_hi, v_lo = float_to_registers(voltage)
        i_reg = int_to_register(current, 0.01)
        t_reg = signed_int_to_register(temperature, 0.1)
        pf_reg = int_to_register(pf, 0.001)
        f_reg = int_to_register(freq, 0.01)

        # Write to data store
        # pymodbus uses 1-based addressing internally in setValues
        values = [v_hi, v_lo, i_reg, t_reg, pf_reg, f_reg]
        self.context[self.unit_id].setValues(3, 0, values)  # FC=3 → holding registers

        self.update_count += 1
        if self.update_count % 20 == 0:  # Log every 5 seconds at 250ms interval
            log.info(
                "Registers: V=%.1fV (0x%04X 0x%04X) I=%.2fA (%d) T=%.1f°C (%d) "
                "PF=%.3f (%d) F=%.2fHz (%d)",
                voltage, v_hi, v_lo,
                current, i_reg,
                temperature, t_reg,
                pf, pf_reg,
                freq, f_reg,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Modbus TCP Slave Simulator — Simulated Energy Meter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python meter_sim.py --port 5020
  python meter_sim.py --port 5020 --fault spike
  python meter_sim.py --port 5021 --unit-id 2 --fault freeze
        """,
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5020, help="TCP port (default: 5020)")
    parser.add_argument("--unit-id", type=int, default=1, help="Modbus unit ID (default: 1)")
    parser.add_argument(
        "--fault",
        choices=["none", "spike", "freeze", "disconnect"],
        default="none",
        help="Fault injection mode",
    )
    parser.add_argument(
        "--update-interval",
        type=float,
        default=0.25,
        help="Register update interval in seconds (default: 0.25)",
    )
    return parser.parse_args()


def build_server_context(unit_id: int, register_count: int = 100):
    """Create a device context compatible with both legacy and modern pymodbus versions."""
    hr_block = ModbusSequentialDataBlock(0, [0] * register_count)
    store = ModbusSlaveContext(
        di=ModbusSequentialDataBlock(0, [0] * register_count),
        co=ModbusSequentialDataBlock(0, [0] * register_count),
        ir=ModbusSequentialDataBlock(0, [0] * register_count),
        hr=hr_block,
    )

    try:
        return ModbusServerContext(devices={unit_id: store}, single=False)
    except TypeError:
        return ModbusServerContext(slaves={unit_id: store}, single=False)


def main():
    args = parse_args()

    log.info("=" * 60)
    log.info(" Modbus TCP Slave Simulator")
    log.info(" Address : %s:%d  Unit ID: %d", args.host, args.port, args.unit_id)
    log.info(" Fault   : %s", args.fault)
    log.info("=" * 60)

    # --- Build Modbus data store ---
    # 6 holding registers (addresses 0-5), initialized to zero
    # Using 100 registers to leave room for expansion
    context = build_server_context(unit_id=args.unit_id)

    # --- Start background register updater ---
    updater = RegisterUpdater(
        context=context,
        unit_id=args.unit_id,
        update_interval=args.update_interval,
        fault_mode=args.fault,
    )
    updater.start()

    # --- Graceful shutdown ---
    def shutdown_handler(signum, frame):
        log.info("Received signal %d, shutting down...", signum)
        updater.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # --- Start Modbus TCP server (blocks) ---
    log.info("Starting Modbus TCP server on %s:%d ...", args.host, args.port)
    try:
        StartTcpServer(
            context=context,
            address=(args.host, args.port),
        )
    except Exception:
        log.exception("Server failed")
        updater.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()
