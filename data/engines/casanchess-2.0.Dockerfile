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
        -DBUILD_NNUE_TOOLS=OFF \
        -DENABLE_LTO=ON \
        -DENABLE_STATIC=ON \
    && cmake --build build --parallel "$(nproc)" --target casanchess \
    && install -Dm755 build/casanchess /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
