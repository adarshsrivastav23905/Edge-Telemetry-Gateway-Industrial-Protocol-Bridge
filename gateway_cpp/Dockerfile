# =============================================================================
#  Dockerfile — Edge Telemetry Gateway (C++ Multi-Stage)
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Build Environment
# -----------------------------------------------------------------------------
FROM debian:bookworm-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    pkg-config \
    libmodbus-dev \
    nlohmann-json3-dev \
    libssl-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp-build

# Build and install Eclipse Paho MQTT C library
RUN git clone --depth 1 -b v1.3.13 https://github.com/eclipse/paho.mqtt.c.git && \
    cd paho.mqtt.c && \
    cmake -Bbuild -H. -DPAHO_WITH_SSL=TRUE -DPAHO_BUILD_SAMPLES=FALSE -DPAHO_BUILD_STATIC=TRUE && \
    cmake --build build --target install && \
    ldconfig

# Build and install Eclipse Paho MQTT C++ library
RUN git clone --depth 1 -b v1.3.2 https://github.com/eclipse/paho.mqtt.cpp.git && \
    cd paho.mqtt.cpp && \
    cmake -Bbuild -H. -DPAHO_BUILD_STATIC=TRUE -DPAHO_BUILD_SAMPLES=FALSE && \
    cmake --build build --target install && \
    ldconfig

# Build Gateway Application
WORKDIR /app
COPY CMakeLists.txt ./
COPY src/ ./src/

RUN mkdir -p build && cd build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release && \
    cmake --build . -j$(nproc)

# -----------------------------------------------------------------------------
# Stage 2: Minimal Production Runtime
# -----------------------------------------------------------------------------
FROM debian:bookworm-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libmodbus5 \
    libssl3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled shared libraries from builder
COPY --from=builder /usr/local/lib/libpaho* /usr/local/lib/
RUN ldconfig

WORKDIR /app

# Copy binary from builder
COPY --from=builder /app/build/edge_gateway ./edge_gateway

# Non-root user
RUN useradd -m -u 1000 gateway && chown -R gateway:gateway /app
USER gateway

ENTRYPOINT ["/app/edge_gateway"]
CMD ["/app/config/gateway_config.json"]
