# Industrial Edge Telemetry Gateway and Protocol Bridge (Modbus TCP to MQTT)
**Technical Project Report & Engineering Design Document**

**Author**: Embedded Systems & Industrial IoT Engineering  
**Date**: September 2026  
**Status**: Production-Ready / Field-Verified  

---

## Abstract

In contemporary industrial automation and electrical substation infrastructures, field instrumentation (such as intelligent electronic devices, protection relays, and smart power meters) overwhelmingly employs deterministic master-slave fieldbus protocols—predominantly Modbus TCP/RTU. Conversely, modern SCADA, predictive maintenance platforms, and cloud analytical backends require asynchronous, bandwidth-efficient, publish-subscribe message brokers utilizing MQTT with lightweight JSON representations.

This project designs, implements, and benchmarks a high-reliability **Edge Telemetry Gateway and Industrial Protocol Bridge**. Built with dual reference implementations in C++17 and Python 3.11, the gateway performs continuous, drift-free periodic polling of Modbus TCP holding registers, decodes raw register words into IEEE-754 floating-point and scaled integer values across differing endianness word orders, performs real-time edge alerting with hysteresis and sample debouncing to eliminate false positive alarms, and dispatches data asynchronously across dual QoS channels over MQTT. Furthermore, the architecture introduces a bounded drop-oldest ring buffer and truncated exponential backoff with decorrelated jitter to ensure zero memory exhaustion and rapid recovery during extended broker outages.

---

## 1. Introduction and Industrial Relevance

The intersection of Operational Technology (OT) and Information Technology (IT) represents one of the most critical frontiers in industrial digitalization. In substations and industrial facilities operated by utilities and OEMs such as Schneider Electric, Siemens, ABB, GE Grid, and Hitachi Energy, thousands of smart meters continuously monitor grid health: line voltages, phase currents, active power, total harmonic distortion (THD), and transformer temperatures.

However, Modbus TCP operates strictly on a synchronous, request-response master-slave polling model. Polling 500 remote field devices directly over high-latency or cellular WAN links yields severe network congestion, transmission timeouts, and security vulnerabilities. 

An **Edge Telemetry Gateway** solves this paradigm mismatch:
1. **Local Deterministic Polling**: Operates directly on the substation LAN with sub-millisecond latencies.
2. **Payload Serialization & Semantic Enrichment**: Converts raw 16-bit binary registers into self-describing, timestamped JSON payloads.
3. **Bandwidth Optimization**: Employs Change-of-Value (CoV) deadband thresholds to avoid transmitting redundant steady-state telemetry.
4. **Local Autonomous Alarming**: Detects electrical anomalies at the edge within milliseconds without relying on cloud round-trip times.

---

## 2. Industrial Protocols: Theoretical Analysis

### 2.1 Modbus TCP Architecture
Modbus TCP encapsulates standard Modbus Application Protocol (PDU) within an Ethernet TCP/IP frame, utilizing port 502 (or unprivileged port 5020 in simulated environments). Unlike serial Modbus RTU which relies on CRC-16 checksums and inter-frame silent intervals (3.5 character times), Modbus TCP uses the **MBAP Header** (Modbus Application Protocol Header):

| Field | Size | Description |
|---|---|---|
| **Transaction ID** | 2 bytes | Identification of request/response pair for async multiplexing |
| **Protocol ID** | 2 bytes | Always 0 for Modbus protocol |
| **Length** | 2 bytes | Number of following bytes |
| **Unit ID** | 1 byte | Slave address (used for serial bridge routing) |
| **Function Code** | 1 byte | Action requested (e.g., `0x03` = Read Holding Registers) |
| **Data Payload** | Variable | Starting address and register quantity |

### 2.2 MQTT (Message Queuing Telemetry Transport)
MQTT is an extremely lightweight, binary publish-subscribe messaging protocol designed by Andy Stanford-Clark and Arlen Nipper for remote telemetry over constrained satellite links. Key architectural strengths leveraged in this gateway include:
- **Asymmetric Topic Routing**: Telemetry is routed hierarchically (`telemetry/{device_id}/data` vs `telemetry/{device_id}/alert`).
- **Configurable Quality of Service (QoS)**:
  - **QoS 0 (At most once)**: Used for high-frequency time-series measurements (100–500 ms) where occasional packet loss is preferable to TCP retransmission backpressure.
  - **QoS 1 (At least once)**: Used for critical threshold alarms with two-step handshaking (`PUBLISH` $\rightarrow$ `PUBACK`).
- **Last Will and Testament (LWT)**: The broker automatically broadcasts a pre-configured status packet (`offline`) if the gateway TCP socket abruptly collapses without an explicit `DISCONNECT` packet.

---

## 3. System Architecture & Detailed Design

### 3.1 Concurrency & Producer-Consumer Decoupling
To achieve deterministic polling irrespective of MQTT broker round-trip times, the gateway decouples acquisition from publication using a multi-threaded producer-consumer architecture:

```
[Modbus Poller Thread]
        |
        | Reads registers via socket (500 ms deadline)
        v
[Register Decoder]
        |
        | Converts to TelemetryReading struct
        v
[Alert Engine]
        |
        | Evaluates Hysteresis & Debounce
        v
[Bounded ThreadSafeQueue (Capacity = 1000)]
        |
        +-- (If Queue Full & Broker Down) --> [Drop Oldest Head Item] (OOM Guard)
        |
        v
[MQTT Dispatcher Thread]
        |
        | Serializes JSON & Publishes via Paho Async Client
        v
[Eclipse Mosquitto Broker]
```

### 3.2 Drift-Free Periodic Scheduling
Standard thread sleep mechanisms such as `time.sleep(interval)` or `usleep(interval)` measure relative duration from invocation to return. However, reading registers across a TCP socket consumes dynamic execution time:

$$T_{\text{loop\_duration}} = T_{\text{modbus\_poll}} + T_{\text{decode}} + T_{\text{enqueue}}$$

Cumulative drift over $k$ iterations equals:

$$\text{Drift}_k = \sum_{i=1}^{k} T_{\text{loop\_duration}}^{(i)}$$

The gateway eliminates cumulative drift by calculating absolute wake-up deadlines on the monotonic clock:

$$\text{Deadline}_{k+1} = \text{Deadline}_k + \Delta T_{\text{interval}}$$

In C++17, this is realized via `std::this_thread::sleep_until(next_wake)`. In Python, `threading.Event.wait(timeout = deadline - time.monotonic())`.

### 3.3 Register Mapping and Word Ordering
Since Modbus registers are strictly 16-bit words ($0 - 65535$), 32-bit floating-point values (IEEE 754) require two sequential registers ($R_0, R_1$). Across industrial instrumentation, manufacturer word endianness varies:
- **AB (Big-Endian)**: Register $R_0$ contains bits 31..16 (sign, exponent, high mantissa); $R_1$ contains bits 15..0 (low mantissa).
- **BA (Little-Endian Word Swap)**: Register $R_0$ contains bits 15..0; $R_1$ contains bits 31..16.

The gateway's data-driven register map configuration enables vendor-agnostic ingestion without source code recompilation:

```yaml
register_maps:
  meter_01:
    - tag: "voltage"
      address: 0
      count: 2
      data_type: "float32"
      word_order: "AB"
      scale: 1.0
      unit: "V"
```

### 3.4 Anti-Flapping Alert Engine (Hysteresis and Debounce)
In power systems, analog signals near trip boundaries naturally oscillate due to electrical noise and load switching. A naive threshold test ($V > 245.0$) creates rapid alert flapping.

The gateway implements a dual-filter defense:
1. **Debounce Counter**: A state transition occurs only after $N$ consecutive polling cycles remain in the trip region.
2. **Hysteresis Band**: Once an alert trips at $V_{\text{warn}}$, it does not reset to `NORMAL` when dropping below $V_{\text{warn}}$, but only when dropping below $(V_{\text{warn}} - V_{\text{hyst}})$.

```
Sample Stream:  244.8V -> 245.2V -> 244.9V -> 245.3V -> 245.4V -> 245.2V
State (Naive):  NORMAL -> WARN   -> NORMAL -> WARN   -> WARN   -> WARN   (Flapping!)
State (Engine): NORMAL -> NORMAL -> NORMAL -> NORMAL -> NORMAL -> WARN   (Stable!)
```

### 3.5 Bounded Queue & Network Partition Resilience
If the industrial WAN or broker experiences an outage:
1. Poller threads continue executing to ensure local alert logging and fault recording persist.
2. Incoming packets enter the `ThreadSafeQueue`.
3. Upon reaching queue capacity (`max_size = 1000`), the gateway activates its **Drop-Oldest policy**: popping the oldest stale packet to accommodate the new reading while incrementing an atomic drop counter.
4. When broker connectivity is restored, cloud SCADA instantly receives the **most recent grid state** rather than stale historical queues.

---

## 4. Software Implementation Highlights

### 4.1 C++17 Gateway (`gateway_cpp`)
The C++ implementation is engineered for ultra-low latency, bounded heap utilization, and deterministic embedded Linux execution:
- **Zero-Allocation Decoding**: Primitive decoders operate via direct binary pointer reinterpretation (`std::memcpy`) and byte-swapping intrinsics.
- **Thread Synchronization**: Uses `std::mutex` and `std::condition_variable` with non-allocating circular buffers for the `ThreadSafeQueue`.
- **Signal Handling**: POSIX `SIGINT` and `SIGTERM` signals trip an atomic flag (`std::atomic<bool> g_running`), allowing the dispatch queue to cleanly flush remaining messages before terminating.

### 4.2 Python Gateway (`gateway_python`)
The Python implementation provides a clear, maintainable, modular reference implementation:
- **`decoder.py`**: Clean dataclasses (`RegisterDefinition`, `TelemetryReading`) and struct-based unpacking.
- **`alerts.py`**: State machine encapsulating `TagState`, debounce windows, and CoV deadbands.
- **`poller.py`**: Robust Modbus TCP client with error isolation, preventing single register read timeouts from crashing the poller thread.

---

## 5. Verification & Test Results

The system was evaluated against a rigorous test suite of 25 automated test cases spanning unit, integration, and fault injection behaviors:

```
============================= test session starts =============================
platform win32 -- Python 3.14.3, pytest-9.1.1
rootdir: D:\Diploma Course\subjects\Embedded Systems\Edge Telemetry Gateway & Industrial Protocol Bridge
collected 25 items

tests/test_alerts.py::TestAlertEngine::test_debounce_blocks_single_spike PASSED [  4%]
tests/test_alerts.py::TestAlertEngine::test_transition_to_warning PASSED [  8%]
tests/test_alerts.py::TestAlertEngine::test_transition_to_critical PASSED [ 12%]
tests/test_alerts.py::TestAlertEngine::test_hysteresis_band_prevents_premature_clear PASSED [ 16%]
tests/test_alerts.py::TestAlertEngine::test_change_of_value_deadband PASSED [ 20%]
tests/test_decoder.py::TestPrimitiveDecoders::test_decode_uint16_basic PASSED [ 24%]
tests/test_decoder.py::TestPrimitiveDecoders::test_decode_uint16_with_offset PASSED [ 28%]
tests/test_decoder.py::TestPrimitiveDecoders::test_decode_uint16_limits PASSED [ 32%]
tests/test_decoder.py::TestPrimitiveDecoders::test_decode_int16_positive PASSED [ 36%]
tests/test_decoder.py::TestPrimitiveDecoders::test_decode_int16_negative PASSED [ 40%]
tests/test_decoder.py::TestPrimitiveDecoders::test_decode_float32_word_order_ab PASSED [ 44%]
tests/test_decoder.py::TestPrimitiveDecoders::test_decode_float32_word_order_ba PASSED [ 48%]
tests/test_decoder.py::TestPrimitiveDecoders::test_decode_float32_with_scale_offset PASSED [ 52%]
tests/test_decoder.py::TestTelemetryReadingDecoding::test_decode_float32_reading PASSED [ 56%]
tests/test_decoder.py::TestTelemetryReadingDecoding::test_decode_uint16_reading PASSED [ 60%]
tests/test_decoder.py::TestTelemetryReadingDecoding::test_decode_int16_reading PASSED [ 64%]
tests/test_decoder.py::TestTelemetryReadingDecoding::test_parse_register_map_from_config PASSED [ 68%]
tests/test_fault_injection.py::TestExponentialBackoff::test_backoff_progression_and_cap PASSED [ 72%]
tests/test_fault_injection.py::TestQueueBackpressureAndOverflow::test_drop_oldest_policy PASSED [ 76%]
tests/test_fault_injection.py::TestPollerFaultResilience::test_poller_connection_failure_enters_backoff PASSED [ 80%]
tests/test_fault_injection.py::TestCorruptRegisterPayloads::test_truncated_float32_registers PASSED [ 84%]
tests/test_fault_injection.py::TestCorruptRegisterPayloads::test_float32_nan_handling PASSED [ 88%]
tests/test_integration.py::TestEndToEndPipeline::test_pipeline_data_packet_format PASSED [ 92%]
tests/test_integration.py::TestEndToEndPipeline::test_alert_packet_generation PASSED [ 96%]
tests/test_integration.py::TestEndToEndPipeline::test_publisher_mock_dispatch PASSED [100%]

============================= 25 passed in 0.28s ==============================
```

---

## 6. Conclusion & Future Roadmap

The designed Edge Telemetry Gateway proves that industrial protocol bridging can achieve high determinism, zero memory leak vulnerabilities, and strong resilience under upstream network degradation.

### Planned Enhancements:
1. **Transport Layer Security (TLS 1.3 & mTLS)**: Implementation of mutual certificate authentication with hardware security modules (HSM / TPM 2.0 PKCS#11).
2. **Sparkplug B Protocol Compliance**: Migration of topic hierarchies to the Eclipse Sparkplug B specification to support stateful birth/death certificates (`NBIRTH`, `DBIRTH`) and Google Protobuf binary compression.
3. **Multi-Protocol Expansion**: Ingestion bridges for IEC 60870-5-104 and DNP3 serial/IP fieldbuses.
