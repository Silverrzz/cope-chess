FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:04d651e078b7c7334709dbd772d40a23c0a5480e93e19521a03020c7d633f2cf https://github.com/Ciekce/stormphrax-nets/releases/download/undertown/undertown.nnue /build/undertown.nnue
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
