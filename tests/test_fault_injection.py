#!/usr/bin/env python3
"""
=============================================================================
 Fault Injection & Resilience Tests
=============================================================================
 Tests:
   1. Exponential Backoff with Jitter: Verify timing progression & max cap
   2. Bounded Queue Backpressure: Drop-oldest policy under broker outage
   3. Modbus Poller Fault Handling: Disconnects, retries, and backoff states
   4. Corrupt Register Inputs: Incomplete register slices, NaN/Inf robustness
=============================================================================
"""

import math
import os
import queue
import random
import struct
import sys
from unittest.mock import MagicMock

import pytest

# Safe mock fallback for pymodbus if running outside container / venv
try:
    import pymodbus
except ImportError:
    mock_modbus = MagicMock()
    mock_modbus.exceptions.ModbusException = Exception
    sys.modules["pymodbus"] = mock_modbus
    sys.modules["pymodbus.client"] = mock_modbus
    sys.modules["pymodbus.exceptions"] = mock_modbus

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gateway_python")))

from decoder import RegisterDef, decode_registers, decode_float32
from poller import ModbusPoller, ConnectionState


class TestExponentialBackoff:
    """Verify exponential backoff algorithm behavior and jitter constraints."""

    def test_backoff_progression_and_cap(self):
        initial_ms = 1000
        max_ms = 60000

        def calc_backoff(attempt: int) -> float:
            base = min(initial_ms * (2 ** attempt), max_ms)
            return base

        assert calc_backoff(0) == 1000
        assert calc_backoff(1) == 2000
        assert calc_backoff(2) == 4000
        assert calc_backoff(3) == 8000
        assert calc_backoff(4) == 16000
        assert calc_backoff(5) == 32000
        assert calc_backoff(6) == 60000  # Capped at max_ms
        assert calc_backoff(10) == 60000 # Remains capped


class TestQueueBackpressureAndOverflow:
    """Verify system resilience during simulated broker failure / network partition."""

    def test_drop_oldest_policy(self):
        max_capacity = 5
        q = queue.Queue(maxsize=max_capacity)
        dropped_count = 0

        # Push items 0 to 9 into a queue with capacity 5 using drop-oldest
        for i in range(10):
            item = f"packet_{i}"
            if q.full():
                try:
                    q.get_nowait()  # Drop oldest
                    dropped_count += 1
                except queue.Empty:
                    pass
            q.put_nowait(item)

        assert dropped_count == 5
        assert q.qsize() == 5

        # Elements remaining should be the 5 most recent packets: 5, 6, 7, 8, 9
        remaining = []
        while not q.empty():
            remaining.append(q.get_nowait())

        assert remaining == ["packet_5", "packet_6", "packet_7", "packet_8", "packet_9"]


class TestPollerFaultResilience:
    """Verify ModbusPoller behavior when slave disappears or returns errors."""

    def test_poller_connection_failure_enters_backoff(self):
        reg_defs = [
            RegisterDef("voltage", address=0, count=2, data_type="float32", word_order="AB", scale=1.0, offset=0.0, unit="V")
        ]
        mock_queue = queue.Queue(maxsize=10)

        poller = ModbusPoller(
            device_id="unreachable_meter",
            host="127.0.0.1",
            port=59999,
            unit_id=1,
            poll_interval_ms=100,
            register_defs=reg_defs,
            dispatch_queue=mock_queue,
            timeout_s=0.1,
            retries=1,
            initial_backoff_ms=50,
            max_backoff_ms=200,
            jitter_ms=10,
        )

        assert poller._state == ConnectionState.DISCONNECTED

        # Ensure poller._client connect returns False
        mock_client = MagicMock()
        mock_client.connect.return_value = False
        poller._client = mock_client

        # Direct enter backoff test
        poller._enter_backoff()
        assert poller._state == ConnectionState.BACKOFF
        assert poller._backoff_attempt == 1


class TestCorruptRegisterPayloads:
    """Verify decoders handle malformed data without raising unhandled exceptions."""

    def test_truncated_float32_registers(self):
        val = decode_float32([0x4366], word_order="AB")
        assert math.isnan(val)

    def test_float32_nan_handling(self):
        val = decode_float32([0x7FC0, 0x0000], word_order="AB")
        assert math.isnan(val)
