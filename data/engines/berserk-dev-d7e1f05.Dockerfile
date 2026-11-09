FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:6fd12bbd95c8658d6d605d844487f07589374a3dc073f4950f310db9d7fd8a7a https://github.com/jhonnold/berserk-networks/releases/download/networks/berserk-6fd12bbd95c8.nn /build/src/berserk-6fd12bbd95c8.nn
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
