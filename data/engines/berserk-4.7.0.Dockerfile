FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build
COPY . .

RUN make -C src pgo \
        ARCH=avx2 \
        CC=clang \
        LIBS="-pthread -lm -static" \
    && install -Dm755 src/berserk /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
