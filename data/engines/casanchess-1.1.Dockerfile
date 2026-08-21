FROM gcc:14.3.0-bookworm AS builder

RUN apt-get update \
    && apt-get install --no-install-recommends -y cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .

RUN cmake -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_TESTS=OFF \
        -DBUILD_TESTS_EXTRA=OFF \
        -DBUILD_EXECUTABLES_EXTRA=OFF \
        -DENABLE_LTO=ON \
        -DENABLE_STATIC=ON \
    && cmake --build build --parallel "$(nproc)" \
    && install -Dm755 build/casanchess /opt/cope/engine \
    && install -Dm644 data/network-20260806.nnue /opt/cope/network-20260806.nnue \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./

ENTRYPOINT ["./engine"]
