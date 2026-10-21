FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .

RUN echo "50c86524390b8e7976ef44d4877789c1283bf3979d5e5ac12d8ce7b2795971c8  quantised-v14.bin" | sha256sum -c - \
    && echo "201b69d562dfc637365a9004e904c14db7a3d1c42372911985d6ebf44a46fe9d  Policy-big.bin" | sha256sum -c - \
    && echo "d5e8b3f2bba06e556f20838adf5b5111da303a384eb3262dad62a88185998fcc  quantised-64.bin" | sha256sum -c - \
    && g++ \
        -std=c++17 \
        -O3 \
        -flto=auto \
        -march=x86-64-v3 \
        -DNDEBUG \
        -pthread \
        -static \
        -DEVALFILE='"quantised-v14.bin"' \
        -DPOLICYFILE='"Policy-big.bin"' \
        -DPOLICYFILE_SMALL='"quantised-64.bin"' \
        -DKOCIOLEK_POLICY_HL=2048 \
        -DPOLICY_SMALL_HL=64 \
        src/*.cpp \
        -o Kociolek \
    && install -Dm755 Kociolek /opt/cope/engine \
    && install -Dm644 quantised-v14.bin /opt/cope/quantised-v14.bin \
    && install -Dm644 Policy-big.bin /opt/cope/Policy-big.bin \
    && install -Dm644 quantised-64.bin /opt/cope/quantised-64.bin \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./

ENTRYPOINT ["./engine"]
