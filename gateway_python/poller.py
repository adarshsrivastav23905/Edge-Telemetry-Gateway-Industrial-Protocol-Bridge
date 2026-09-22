#!/usr/bin/env python3
"""
=============================================================================
 Poller — Drift-Free Modbus Register Polling Engine
=============================================================================
 Reads holding registers from a Modbus TCP slave device on a fixed interval
 using a drift-free scheduler based on the monotonic clock.

 Why not just sleep(0.5)?
   If polling + decoding takes 20 ms, then sleep(500 ms) gives a 520 ms
   cycle that drifts over time. Instead we compute absolute wake-up times:
     next_wake = start + N * interval
   and sleep only the remaining delta, so the long-term average is exact.

 Connection state machine:
   DISCONNECTED → CONNECTING → CONNECTED → (on failure) → BACKOFF → CONNECTING
   Backoff: delay = min(initial * 2^attempt + jitter, max_delay)

 Thread safety:
   The poller runs on its own thread. Decoded readings are pushed into a
   thread-safe bounded queue shared with the dispatcher/publisher.
=============================================================================
"""

import enum
import logging
import random
import threading
import time
from typing import Optional

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

from decoder import RegisterDefinition, TelemetryReading, decode_registers

log = logging.getLogger("Poller")


class ConnectionState(enum.Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    BACKOFF = "BACKOFF"


class ModbusPoller:
    """
    Polls a single Modbus TCP slave device on a fixed interval, decodes the
    registers, and pushes TelemetryReading objects into a dispatch queue.
    """

    def __init__(
        self,
        device_id: str,
        host: str,
        port: int,
        unit_id: int,
        poll_interval_ms: int,
        register_defs: list[RegisterDefinition],
        dispatch_queue,                # queue.Queue (thread-safe)
        timeout_s: float = 1.0,
        retries: int = 3,
        initial_backoff_ms: int = 1000,
        max_backoff_ms: int = 60000,
        jitter_ms: int = 500,
    ):
        self.device_id = device_id
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.poll_interval_s = poll_interval_ms / 1000.0
        self.register_defs = register_defs
        self.dispatch_queue = dispatch_queue
        self.timeout_s = timeout_s
        self.retries = retries
        self.initial_backoff_ms = initial_backoff_ms
        self.max_backoff_ms = max_backoff_ms
        self.jitter_ms = jitter_ms

        # Connection state
        self._state = ConnectionState.DISCONNECTED
        self._client: Optional[ModbusTcpClient] = None
        self._backoff_attempt = 0

        # Threading
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=f"Poller-{device_id}"
        )

        # Counters
        self.poll_count = 0
        self.poll_errors = 0
        self.reconnect_count = 0

    # ----- Public API -----

    def start(self):
        self._thread.start()
        log.info("[%s] Poller started (interval=%dms, host=%s:%d, unit=%d)",
                 self.device_id, int(self.poll_interval_s * 1000),
                 self.host, self.port, self.unit_id)

    def stop(self):
        self._stop_event.set()
        self._thread.join(timeout=5.0)
        self._disconnect()
        log.info("[%s] Poller stopped — polls=%d errors=%d reconnects=%d",
                 self.device_id, self.poll_count, self.poll_errors,
                 self.reconnect_count)

    # ----- Connection state machine -----

    def _connect(self) -> bool:
        """Attempt to connect to the Modbus slave."""
        self._state = ConnectionState.CONNECTING
        log.info("[%s] Connecting to %s:%d ...", self.device_id, self.host, self.port)

        try:
            self._client = ModbusTcpClient(
                host=self.host,
                port=self.port,
                timeout=self.timeout_s,
                retries=self.retries,
            )
            if self._client.connect():
                self._state = ConnectionState.CONNECTED
                self._backoff_attempt = 0
                self.reconnect_count += 1
                log.info("[%s] Connected successfully", self.device_id)
                return True
            else:
                log.warning("[%s] Connection refused", self.device_id)
                self._enter_backoff()
                return False
        except Exception as e:
            log.error("[%s] Connection error: %s", self.device_id, e)
            self._enter_backoff()
            return False

    def _disconnect(self):
        """Close the Modbus connection cleanly."""
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        self._state = ConnectionState.DISCONNECTED

    def _enter_backoff(self):
        """Enter backoff state with exponential delay + jitter."""
        self._state = ConnectionState.BACKOFF
        delay_ms = min(
            self.initial_backoff_ms * (2 ** self._backoff_attempt)
            + random.randint(0, self.jitter_ms),
            self.max_backoff_ms,
        )
        self._backoff_attempt += 1
        log.warning("[%s] Backing off for %d ms (attempt %d)",
                    self.device_id, delay_ms, self._backoff_attempt)
        self._stop_event.wait(timeout=delay_ms / 1000.0)

    # ----- Polling loop -----

    def _run(self):
        """Main polling loop with drift-free scheduling."""
        # Establish initial connection
        while not self._stop_event.is_set():
            if self._connect():
                break

        # Drift-free periodic loop
        next_wake = time.monotonic()
        while not self._stop_event.is_set():
            next_wake += self.poll_interval_s

            if self._state == ConnectionState.CONNECTED:
                self._poll_all_registers()
            else:
                # Try to reconnect
                self._connect()

            # Sleep until next scheduled wake-up
            remaining = next_wake - time.monotonic()
            if remaining > 0:
                self._stop_event.wait(timeout=remaining)

    def _poll_all_registers(self):
        """Read all register definitions and push decoded readings to queue."""
        for reg_def in self.register_defs:
            if self._stop_event.is_set():
                return

            try:
                result = self._client.read_holding_registers(
                    address=reg_def.address,
                    count=reg_def.count,
                    slave=self.unit_id,
                )

                if result.isError():
                    log.warning("[%s] Read error for '%s' at addr %d: %s",
                                self.device_id, reg_def.tag, reg_def.address, result)
                    self.poll_errors += 1
                    continue

                # Decode the raw registers
                reading = decode_registers(
                    device_id=self.device_id,
                    reg_def=reg_def,
                    raw_values=result.registers,
                )

                # Push to dispatch queue (non-blocking)
                self._enqueue_reading(reading)

            except ModbusException as e:
                log.error("[%s] Modbus exception reading '%s': %s",
                          self.device_id, reg_def.tag, e)
                self.poll_errors += 1
                self._disconnect()
                self._enter_backoff()
                return  # skip remaining registers this cycle

            except Exception as e:
                log.error("[%s] Unexpected error reading '%s': %s",
                          self.device_id, reg_def.tag, e)
                self.poll_errors += 1
                self._disconnect()
                self._enter_backoff()
                return

        self.poll_count += 1
        if self.poll_count % 10 == 0:
            log.debug("[%s] Poll #%d complete", self.device_id, self.poll_count)

    def _enqueue_reading(self, reading: TelemetryReading):
        """Push a reading into the dispatch queue with drop-oldest on overflow."""
        try:
            if self.dispatch_queue.full():
                # Drop oldest to make room (most recent data is more valuable)
                try:
                    dropped = self.dispatch_queue.get_nowait()
                    log.warning("[%s] Queue full — dropped oldest reading: %s/%s",
                                self.device_id, dropped.device_id, dropped.tag)
                except Exception:
                    pass
            self.dispatch_queue.put_nowait(reading)
        except Exception as e:
            log.error("[%s] Failed to enqueue reading: %s", self.device_id, e)
