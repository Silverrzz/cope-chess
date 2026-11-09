FROM gcc:14.3.0-bookworm AS builder

RUN apt-get update \
    && apt-get install --no-install-recommends -y cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .
ADD --checksum=sha256:f79b72f50d7c4899fefa32949e665cd35cf2b3633e25d55a507659c4ee8a38cc https://github.com/aronpetko/integral-networks/releases/download/yveltal/yveltal.nnue /build/yveltal.nnue

RUN cmake -S . -B out -DCMAKE_BUILD_TYPE=Release -DBUILD_AVX2_BMI2=ON -DPGO_MODE=OFF -DEVALFILE=/build/yveltal.nnue \
        -DCMAKE_EXE_LINKER_FLAGS="-static -static-libgcc -static-libstdc++" \
    && cmake --build out --parallel "$(nproc)" \
    && install -Dm755 out/integral /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch
WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine
ENTRYPOINT ["./engine"]
