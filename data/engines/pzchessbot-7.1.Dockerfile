FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:915d4e591ce2e760d41d7023fef2bf27775b8d955bbee7468f458a89c674c1aa https://github.com/kevlu8/PZChessBot/releases/download/v7.1/pznet53.nnue /build/nnue.bin
COPY . .

RUN make -j"$(nproc)" v3 \
        CXX=g++ \
        EXE=pzchessbot \
        LDFLAGS="-static -static-libgcc -static-libstdc++" \
    && install -Dm755 pzchessbot /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
