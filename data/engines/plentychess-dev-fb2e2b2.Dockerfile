FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:e2fefcd2ffe9d8f891a910592fd5713143d3d59673bb41056865fc5225dae5df https://github.com/Yoshie2000/PlentyNetworks/releases/download/0179r/0179r.bin /build/0179r.bin
COPY . .

RUN make -j"$(nproc)" \
        CXX=g++ \
        CC=gcc \
        arch=avx2 \
        EXE=PlentyChess \
        LDFLAGS="-static -static-libgcc -static-libstdc++" \
    && install -Dm755 PlentyChess /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
