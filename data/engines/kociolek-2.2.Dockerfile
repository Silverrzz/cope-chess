FROM gcc:14.3.0-bookworm AS builder

RUN apt-get update \
    && apt-get install --no-install-recommends -y python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .

RUN echo "76265d0f846f508ab4a3c91b75d70854ce15cc2e21a400d519cd8b8c2be6be9b  768-1024x2-1-8.bin" | sha256sum -c - \
    && echo "3f462ae16f42b47b68b1f3182c2cbba67e885ed0dcff5c2863cf81f0248a6d2f  quantised-dual-layer-hard-2048.bin" | sha256sum -c - \
    && echo "d5e8b3f2bba06e556f20838adf5b5111da303a384eb3262dad62a88185998fcc  quantised-64.bin" | sha256sum -c - \
    && make -j"$(nproc)" \
        CXX=g++ \
        PYTHON=python3 \
        TARGET=Kociolek \
    && install -Dm755 Kociolek /opt/cope/engine \
    && install -Dm644 768-1024x2-1-8.bin /opt/cope/768-1024x2-1-8.bin \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./

ENTRYPOINT ["./engine"]
