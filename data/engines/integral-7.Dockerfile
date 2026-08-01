FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:804baa11f91451189ae13c8ffe686243bf0f694b0e6046e9c1b1bc6bf6853ac4 https://github.com/aronpetko/integral-networks/releases/download/dialga-v2/dialga-v2.nnue /build/dialga-v2.nnue
COPY . .

RUN install -d /opt/cope \
    && gcc -std=c11 -O3 -flto=auto -DNDEBUG \
        -Ithird-party/fathom \
        -c third-party/fathom/tbprobe.c \
        -o /tmp/tbprobe.o \
    && g++ -std=c++20 -O3 -flto=auto -funroll-loops -DNDEBUG -pthread \
        -march=x86-64-v3 -mno-bmi2 \
        -DBUILD_AVX2 \
        -DFMT_HEADER_ONLY \
        -DEVALFILE=\"/build/dialga-v2.nnue\" \
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
