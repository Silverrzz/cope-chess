FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:c836e4b42d7282f71265df5422c73f06f6437b6d8052371f8dc32d250a01c056 https://github.com/Orbital-Web/Raphael-Net/releases/download/yogsothoth_v3/yogsothoth_v3.nnue /build/yogsothoth_v3.nnue
COPY . .

RUN make -j4 perm \
        CXX=g++ \
        ARCH=avx2 \
        DEBUG=release \
        PGO=off \
    && ./perm yogsothoth_v3.nnue \
    && make -j4 uci \
        CXX=g++ \
        ARCH=avx2 \
        DEBUG=release \
        PGO=off \
    && install -Dm755 uci /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
