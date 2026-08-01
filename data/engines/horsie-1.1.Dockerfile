FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:99a74482dd6a4df7ad8ae23b4653ed9c5daeec1a8db59cbc40cc71d830e3c955 https://github.com/liamt19/lizard-nets/releases/download/net-015-2048x16x32-132qb-z/net-015-2048x16x32-132qb-z.bin /build/net-015-2048x16x32-132qb-z.bin
COPY . .

RUN make v3 \
        CXX=g++ \
        LDFLAGS="-pthread -static -static-libgcc -static-libstdc++" \
    && install -Dm755 horsie /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
