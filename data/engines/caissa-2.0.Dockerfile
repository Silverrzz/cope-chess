FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:ee9f6bbb0ef6cd1f38a13cf095340968c2ad18d0fa1359d493043e61acc829fa https://github.com/Witek902/Caissa-Nets/releases/download/eval-ml-8-152B-spsa/eval-ml-8-152B-spsa.pnn /build/data/neuralNets/eval-ml-8-152B-spsa.pnn
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
    && install -Dm755 src/caissa-2.0-x64-bmi2 /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
