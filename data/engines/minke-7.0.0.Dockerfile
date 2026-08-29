FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:6eba95de4598b009328b18585c8a3c7ad8ad22bd797ed0a8f20a0627f19f37af https://github.com/enfmarinho/MinkeNets/releases/download/minke39/minke39.nnue /build/minke39.nnue
COPY . .

RUN make -j"$(nproc)" bmi2 \
        CXX=g++ \
        EVALFILE=minke39.nnue \
        EXE=minke \
        LDFLAGS="-flto=auto -pthread -static" \
    && install -Dm755 minke /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
