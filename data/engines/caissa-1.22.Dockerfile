FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:f27f96200c534b6fdb63e7444d61cccbb379cf78d439ba66b0d81d6d9bf78e41 https://github.com/Witek902/Caissa/releases/download/1.22/caissa-1.22-x64-avx2-bmi2.exe /tmp/caissa.exe
COPY . .

RUN mkdir -p data/neuralNets \
    && tail -c +1017857 /tmp/caissa.exe | head -c 17336960 > data/neuralNets/eval-57.pnn \
    && echo "e366a3718d83b4aeedd3b86e5b703160ca1c8ab9c39d2e1bf699243fac036b7a  data/neuralNets/eval-57.pnn" | sha256sum -c - \
    && make -C src -j"$(nproc)" bmi2 \
        CC=clang++ \
        EXE=caissa \
        FLAGS="-Wall -Wno-unused-function -Wno-switch -Wno-attributes -Wno-missing-field-initializers -Wno-multichar -s -flto=thin -fuse-ld=lld -std=c++20 -O3 -funroll-loops -static" \
    && install -Dm755 src/caissa-1.22-x64-bmi2 /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
