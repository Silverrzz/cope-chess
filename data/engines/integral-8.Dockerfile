FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:bf11ca1a4c8d27d86419a4c64d84a553d7a8023f5528de1788df35e8ecc067f2 https://github.com/aronpetko/integral-networks/releases/download/haxorus/haxorus.nnue /build/haxorus.nnue
COPY . .

RUN install -d /opt/cope \
    && g++ -std=c++20 -O3 -DNDEBUG \
        -march=x86-64-v3 -mno-bmi2 \
        -DBUILD_AVX2 \
        -DFMT_HEADER_ONLY \
        -Ithird-party/fathom \
        -Ithird-party/fmt/include \
        preprocess/net_processing.cc \
        -o /tmp/preprocess \
    && /tmp/preprocess /build/haxorus.nnue /build/processed.nnue \
    && gcc -std=c11 -O3 -flto=auto -DNDEBUG \
        -Ithird-party/fathom \
        -c third-party/fathom/tbprobe.c \
        -o /tmp/tbprobe.o \
    && g++ -std=c++20 -O3 -flto=auto -funroll-loops -DNDEBUG -pthread \
        -march=x86-64-v3 -mno-bmi2 \
        -DBUILD_AVX2 \
        -DFMT_HEADER_ONLY \
        -DEVALFILE=\"/build/processed.nnue\" \
        -Ithird-party/fathom \
        -Ithird-party/fmt/include \
        $(find src -name '*.cc' -print) \
        /tmp/tbprobe.o \
        -static -static-libgcc -static-libstdc++ \
        -o /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
