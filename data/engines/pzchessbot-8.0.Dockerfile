FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .
ADD --checksum=sha256:56470c0583a36f5f2c9058a939d40cd1f7ba1813d10f5c19bc0431be3edcfd7e https://github.com/kevlu8/PZChessBot/releases/download/v8.0/pznet65.nnue /build/nnue.bin

RUN make -j"$(nproc)" v3 \
        CXX=g++ \
        VERSION=v8.0 \
        NUMA_NODES=1 \
        LDFLAGS="-static -static-libgcc -static-libstdc++ -Wl,-z,stack-size=8388608" \
    && install -Dm755 pzchessbot /opt/cope/engine \
    && install -Dm644 LICENSE /opt/cope/LICENSE \
    && install -Dm644 Pyrrhic/LICENSE /opt/cope/licenses/Pyrrhic \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./

ENTRYPOINT ["./engine"]
