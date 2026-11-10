FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .
RUN make -C src -j"$(nproc)" build ARCH=x86-64-modern EXTRALDFLAGS=-static \
    && install -Dm755 src/stockfish /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine
ENTRYPOINT ["./engine"]
