FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:2330b8aa9dafe86f5e5d1ceed14d467fec73611a9bdcf5885998dcff5b033172 https://github.com/Ciekce/stormphrax-nets/releases/download/net096_255_128_q6/net096_255_128_q6.nnue /build/net096_255_128_q6.nnue
COPY . .

RUN make -j"$(nproc)" avx2-bmi2 \
        EXE=stormphrax \
        LDFLAGS="-pthread -fuse-ld=lld -static" \
    && install -Dm755 stormphrax /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
