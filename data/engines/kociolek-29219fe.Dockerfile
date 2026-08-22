FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .

RUN echo "76265d0f846f508ab4a3c91b75d70854ce15cc2e21a400d519cd8b8c2be6be9b  768-1024x2-1-8.bin" | sha256sum -c - \
    && echo "3f462ae16f42b47b68b1f3182c2cbba67e885ed0dcff5c2863cf81f0248a6d2f  quantised-dual-layer-hard-2048.bin" | sha256sum -c - \
    && echo "d5e8b3f2bba06e556f20838adf5b5111da303a384eb3262dad62a88185998fcc  quantised-64.bin" | sha256sum -c - \
    && g++ \
        -std=c++17 \
        -O3 \
        -flto=auto \
        -march=x86-64-v3 \
        -DNDEBUG \
        -pthread \
        -static \
        -DEVALFILE='"768-1024x2-1-8.bin"' \
        -DPOLICYFILE='"quantised-dual-layer-hard-2048.bin"' \
        -DPOLICYFILE_SMALL='"quantised-64.bin"' \
        -DKOCIOLEK_POLICY_HL=2048 \
        -DPOLICY_SMALL_HL=64 \
        src/*.cpp \
        -o Kociolek \
    && install -Dm755 Kociolek /opt/cope/engine \
    && install -Dm644 768-1024x2-1-8.bin /opt/cope/768-1024x2-1-8.bin \
    && install -Dm644 quantised-dual-layer-hard-2048.bin /opt/cope/quantised-dual-layer-hard-2048.bin \
    && install -Dm644 quantised-64.bin /opt/cope/quantised-64.bin \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./

ENTRYPOINT ["./engine"]
