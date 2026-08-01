FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build
COPY . .

RUN make -C src -j"$(nproc)" fast \
        build=x86-64-avx2 \
        CLANG_CC=clang \
        LDFLAGS="-fuse-ld=lld -static" \
    && install -Dm755 src/Potential /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
