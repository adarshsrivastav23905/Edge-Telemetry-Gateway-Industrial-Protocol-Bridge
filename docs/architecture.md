# Edge Telemetry Gateway & Industrial Protocol Bridge — System Architecture

## 1. Executive Summary

In industrial automation and smart grid substations, legacy field instrumentation communicates over deterministic fieldbus protocols like **Modbus TCP/RTU**, **IEC 60870-5-104**, or **DNP3**. Enterprise IT, SCADA, and cloud analytics systems require asynchronous, publish-subscribe protocols such as **MQTT** with structured JSON payloads.

This Edge Telemetry Gateway serves as an industrial protocol converter and intelligent edge processor. It bridges the OT (Operational Technology) domain and the IT (Information Technology) domain with deterministic polling, zero-copy register decoding, local threshold evaluation, hysteresis anti-flapping, queue-based backpressure relief, and automatic reconnection backoff.

```
+---------------------------------------------------------------------------------------+
|                                    OT DOMAIN                                          |
|                                                                                       |
|   +---------------------+   +---------------------+   +---------------------+         |
|   |  Feeder Meter 01    |   |  Transformer RTU    |   |  Generator Substation|        |
|   |  Modbus TCP (5020)  |   |  Modbus TCP (5021)  |   |  Modbus TCP (5022)  |         |
+---+----------+----------+---+----------+----------+---+----------+----------+---------+
               |                         |                         |
               +-------------------------+-------------------------+
                                         | Modbus TCP (Ethernet / 802.3)
                                         v
+---------------------------------------------------------------------------------------+
|                    EDGE TELEMETRY GATEWAY (C++17 / Python Runtime)                    |
|                                                                                       |
|   +-------------------------------------------------------------------------------+   |
|   |                           Poller Thread Pool                                  |   |
|   |   - Drift-Free Periodic Scheduler (std::chrono::steady_clock)                 |   |
|   |   - Connection State Machine (DISCONNECTED -> CONNECTING -> CONNECTED)       |   |
|   |   - libmodbus TCP Driver with configurable retries & socket timeouts          |   |
|   +---------------------------------------+---------------------------------------+   |
|                                           | Raw Register Words (uint16_t[])           |
|                                           v                                           |
|   +-------------------------------------------------------------------------------+   |
|   |                           Data-Driven Decoder                                 |   |
|   |   - Register mapping via YAML / JSON configuration                            |   |
|   |   - Word Order translation (AB Big-Endian vs BA Little-Endian)                |   |
|   |   - IEEE 754 float32 reconstruction & signed 2's complement int16 scaling     |   |
|   +---------------------------------------+---------------------------------------+   |
|                                           | TelemetryReading                          |
|                                           v                                           |
|   +-------------------------------------------------------------------------------+   |
|   |                           Alert & Filtering Engine                            |   |
|   |   - Per-tag Warning & Critical thresholds                                     |   |
|   |   - Hysteresis band prevention of boundary oscillation                       |   |
|   |   - Debounce sample window (anti-flapping filter)                             |   |
|   |   - Change-of-Value (CoV) deadband filter (bandwidth optimization)            |   |
|   +---------------------------------------+---------------------------------------+   |
|                                           | Normal Telemetry & Alert Events           |
|                                           v                                           |
|   +-------------------------------------------------------------------------------+   |
|   |                    Bounded Thread-Safe Queue (Ring Buffer)                    |   |
|   |   - std::mutex + std::condition_variable                                      |   |
|   |   - Drop-oldest policy on broker backpressure (prevents OOM)                  |   |
|   |   - Atomic counters (enqueued, dequeued, dropped)                             |   |
|   +---------------------------------------+---------------------------------------+   |
|                                           |                                           |
|                                           v                                           |
|   +-------------------------------------------------------------------------------+   |
|   |                         MQTT Dispatcher Thread                                |   |
|   |   - Async Paho MQTT Client                                                    |   |
|   |   - Dual-QoS routing: Data (QoS 0), Alerts (QoS 1)                            |   |
|   |   - Exponential reconnect backoff with decorrelated jitter                    |   |
|   |   - Last Will and Testament (LWT) for heartbeat state                         |   |
|   +---------------------------------------+---------------------------------------+   |
+-------------------------------------------|-------------------------------------------+
                                            | MQTT over TCP/TLS (1883 / 8883)
                                            v
+---------------------------------------------------------------------------------------+
|                                    IT DOMAIN                                          |
|                                                                                       |
|   +-------------------------------------------------------------------------------+   |
|   |                     Eclipse Mosquitto / EMQX MQTT Broker                      |   |
|   +---------------------------------------+---------------------------------------+   |
|                                           |                                           |
|                   +-----------------------+-----------------------+                   |
|                   |                                               |                   |
|                   v                                               v                   |
|   +-------------------------------+               +-------------------------------+   |
|   |   Cloud SCADA / Grafana /     |               |   Emergency Alarm Console     |   |
|   |   Time-Series DB (InfluxDB)   |               |   PagerDuty / Webhooks        |   |
|   |   telemetry/{id}/data (QoS 0) |               |   telemetry/{id}/alert (QoS 1)|   |
|   +-------------------------------+               +-------------------------------+   |
+---------------------------------------------------------------------------------------+
```

---

## 2. Core Subsystems

### 2.1 Polling Subsystem & Drift-Free Scheduler

Industrial polling requires strict interval consistency. Conventional implementations using naive `sleep(interval)` suffer from **cumulative execution drift**:

$$\text{Period}_i = T_{\text{poll}} + T_{\text{modbus\_latency}} + T_{\text{decode}}$$

Over hours of operation, this introduces severe timestamp jitter.

The gateway implements absolute monotonic deadline scheduling:

```cpp
auto next_wake = std::chrono::steady_clock::now();
const auto interval = std::chrono::milliseconds(cfg.poll_interval_ms);

while (g_running) {
    next_wake += interval;
    poll_all_registers();
    std::this_thread::sleep_until(next_wake);
}
```

If the execution time $T_{\text{exec}} < T_{\text{interval}}$, the thread sleeps for exactly $(T_{\text{interval}} - T_{\text{exec}})$. If a network delay causes $T_{\text{exec}} \ge T_{\text{interval}}$, the poller immediately proceeds without accumulating drift.

---

### 2.2 Register Decoding & Endianness Management

Modbus holding registers are 16-bit unsigned integers. Modern smart meters represent floating-point quantities (e.g., active power, line voltage) using two consecutive registers (32 bits, IEEE 754).

Different device manufacturers pack these registers in different word orders:
- **AB (Big-Endian)**: High 16-bit word at `Address N`, Low 16-bit word at `Address N+1`. Common in ABB, Schneider PowerLogic, Yokogawa.
- **BA (Little-Endian)**: Low 16-bit word at `Address N`, High 16-bit word at `Address N+1`.

```
IEEE 754 Float: 230.5 V -> 0x43668000
-------------------------------------------------
Word Order AB:
  Register[0] = 0x4366 (17254)  [High Word]
  Register[1] = 0x8000 (32768)  [Low Word]

Word Order BA:
  Register[0] = 0x8000 (32768)  [Low Word]
  Register[1] = 0x4366 (17254)  [High Word]
-------------------------------------------------
```

The gateway handles both modes with zero heap allocations during register unpacking.

---

### 2.3 Alerting, Hysteresis, and Debounce Engine

#### Hysteresis Band
In electrical systems, voltage and temperature frequently hover near trip limits. Without hysteresis, noise triggers rapid alert oscillations ("flapping"), flooding SCADA logs and dispatch queues.

The engine enforces an asymmetric reset band:
- **Trip Condition**: $V \ge V_{\text{threshold}}$
- **Clear Condition**: $V < (V_{\text{threshold}} - V_{\text{hysteresis}})$

```
Voltage
   ^
   |        Trip: 245.0 V
---+----------------------------------------- [WARNING FIRED]
   |        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Hysteresis Band (5.0 V)
   |        Clear: 240.0 V
---+----------------------------------------- [ALERT CLEARED]
   |
   +-----------------------------------------> Time
```

#### Debounce Counter
To ignore transient spikes (e.g., motor inrush current lasting 50 ms), the reading must stay in the alarm state for $N$ consecutive polling samples before the alert event transitions.

---

### 2.4 Backpressure Relief: Bounded Thread-Safe Queue

When an upstream network outage disconnects the MQTT broker, poller threads continue generating readings. An unbounded queue would inevitably cause an Out-Of-Memory (OOM) panic on embedded edge devices (e.g., Raspberry Pi CM4 or Moxa UC-8100).

The gateway uses a bounded queue with a **Drop-Oldest** policy:
1. When full, the oldest telemetry packet at the head of the queue is discarded.
2. The latest real-time reading is appended to the tail.
3. An atomic `drop_count` counter is incremented.

This guarantees:
- Memory usage is strictly bounded ($O(1)$ space).
- When the MQTT connection recovers, SCADA receives the **freshest available operational telemetry** rather than stale hour-old data.

---

### 2.5 MQTT Reconnect with Exponential Backoff and Jitter

To avoid the **thundering herd problem** when a substation network switch reboots, both Modbus and MQTT connection layers implement truncated exponential backoff with random jitter:

$$T_{\text{delay}} = \min\left(T_{\text{initial}} \times 2^{\text{attempt}} + \text{Uniform}(0, T_{\text{jitter}}),\, T_{\text{max}}\right)$$

Default configuration:
- $T_{\text{initial}} = 1000\text{ ms}$
- $T_{\text{max}} = 60000\text{ ms}$
- $T_{\text{jitter}} = 500\text{ ms}$

---

## 3. Telemetry JSON Schemas

### 3.1 Normal Telemetry (`telemetry/{device_id}/data`)
- **MQTT QoS**: 0
- **Retain**: false

```json
{
  "device_id": "meter_01",
  "tag": "voltage",
  "value": 231.42,
  "unit": "V",
  "timestamp": "2026-09-17T00:15:30.125Z",
  "raw_registers": [17254, 27525]
}
```

### 3.2 Threshold Alert (`telemetry/{device_id}/alert`)
- **MQTT QoS**: 1
- **Retain**: false

```json
{
  "device_id": "meter_01",
  "tag": "voltage",
  "level": "CRITICAL",
  "value": 254.80,
  "threshold": 253.0,
  "unit": "V",
  "message": "voltage=254.80V exceeds CRITICAL threshold 253.0V",
  "timestamp": "2026-09-17T00:15:31.000Z"
}
```

### 3.3 Gateway Status LWT (`telemetry/{client_id}/status`)
- **MQTT QoS**: 1
- **Retain**: true

```json
{
  "status": "online",
  "uptime_s": 3600,
  "version": "1.0.0"
}
```
*(On abrupt disconnect, Mosquitto broker publishes the Last Will payload: `"offline"`).*
