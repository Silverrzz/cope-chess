FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:663e0531ac71c6c7c6d3bc4f1fac923bf69136f9786fa1da5c4ac25382aee54b https://github.com/Yoshie2000/PlentyNetworks/releases/download/0178r/0178r.bin /build/0178r.bin
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
