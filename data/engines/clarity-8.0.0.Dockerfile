FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build
COPY . .

ADD --checksum=sha256:bad01ac24275e220ec12fee2a20e9ad0af7e4caceb883f8e4d5606e768127be8 https://github.com/Vast342/Clarity-nets/releases/download/cn_030/cn_030.nnue /build/cn_030.nnue

RUN make \
        ARCH_LEVEL=v3 \
        CXX=clang++ \
        LDFLAGS="-static -fuse-ld=lld" \
    && install -Dm755 Clarity-v3 /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
