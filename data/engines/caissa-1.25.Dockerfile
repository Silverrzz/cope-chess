FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:51929274a45cfc3057c35b07087ee9806e482ddcf64f0659eedcdabf0fea51ff https://github.com/Witek902/Caissa/releases/download/1.25/caissa-1.25-x64-avx2-bmi2.exe /tmp/caissa.exe
COPY . .

RUN mkdir -p data/neuralNets \
    && tail -c +1005569 /tmp/caissa.exe | head -c 50331648 > data/neuralNets/eval-71.pnn \
    && echo "6629611ed2ab8ba84658fc854175356c497fa3ca3c73c57890a6cd073f7a69ca  data/neuralNets/eval-71.pnn" | sha256sum -c - \
    && sed -i \
        -e '/for (const char\* testPosition : testPositions)/i\    uint32_t benchmarkPosition = 0;' \
        -e '/for (const char\* testPosition : testPositions)/,+1 s/{/{\n        if (benchmarkPosition++ == 48)\n        {\n            break;\n        }/' \
        src/frontend/UCI.cpp \
    && sed -i 's/searchParam.limits.maxDepth = static_cast<uint16_t>(depth);/searchParam.limits.maxDepth = static_cast<uint16_t>(depth);\n        searchParam.limits.maxNodes = 1000000;/' src/frontend/UCI.cpp \
    && make -C src -j"$(nproc)" bmi2 \
        CC=clang++ \
        EXE=caissa \
        FLAGS="-Wall -Wno-unused-function -Wno-switch -Wno-attributes -Wno-missing-field-initializers -Wno-multichar -s -flto=thin -fuse-ld=lld -std=c++20 -O3 -funroll-loops -static" \
    && install -Dm755 src/caissa-1.25-x64-bmi2 /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
