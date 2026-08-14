FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:22249de582912f46f73f7cf7410d6d72eccc77696b0b857e99b97a45f3f37116 https://github.com/Witek902/Caissa-Nets/releases/download/eval-82-383B/eval-82-383B.pnn /build/data/neuralNets/eval-82-383B.pnn
COPY . .

RUN sed -i \
        -e '/for (const char\* testPosition : testPositions)/i\    uint32_t benchmarkPosition = 0;' \
        -e '/for (const char\* testPosition : testPositions)/,+1 s/{/{\n        if (benchmarkPosition++ == 48)\n        {\n            break;\n        }/' \
        src/frontend/UCI.cpp \
    && sed -i 's/searchParam.limits.maxDepth = static_cast<uint16_t>(depth);/searchParam.limits.maxDepth = static_cast<uint16_t>(depth);\n        searchParam.limits.maxNodes = 1000000;/' src/frontend/UCI.cpp \
    && make -C src -j"$(nproc)" bmi2 \
        CC=clang++ \
        EXE=caissa \
        FLAGS="-Wall -Wno-unused-function -Wno-switch -Wno-attributes -Wno-missing-field-initializers -Wno-multichar -s -flto=thin -fuse-ld=lld -std=c++20 -O3 -funroll-loops -static" \
    && install -Dm755 src/caissa-1.26-x64-bmi2 /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
