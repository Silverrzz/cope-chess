FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:9b84c340af7e45f6e07f0046235ccb327f4ae0840c8ee2c4b97b99121e5c5084 https://github.com/jhonnold/berserk-networks/releases/download/networks/berserk-9b84c340af7e.nn /build/src/berserk-9b84c340af7e.nn
COPY . .

RUN make -C src -j"$(nproc)" build \
        ARCH=avx2 \
        CC=clang \
        LIBS="-pthread -lm -static" \
    && install -Dm755 src/berserk /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
