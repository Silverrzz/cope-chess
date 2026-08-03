FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:269209e867b0220e98a3ac34609b3fb8759b2f9584dc26ddfc61c97c23a25779 https://github.com/enfmarinho/MinkeNets/releases/download/beluga/beluga.nnue /build/beluga.nnue
COPY . .

RUN make -j"$(nproc)" bmi2 \
        CXX=g++ \
        EVALFILE=beluga.nnue \
        EXE=minke \
        LDFLAGS="-pthread -static" \
    && install -Dm755 minke /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
