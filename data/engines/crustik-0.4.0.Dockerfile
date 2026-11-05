FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .

RUN make -j"$(nproc)" \
        CC=gcc \
        ARCH="-march=x86-64-v3 -mtune=generic" \
        LDFLAGS="-flto=auto -static" \
    && install -Dm755 crustik /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
