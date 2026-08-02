FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .

RUN make -j"$(nproc)" \
        CC=gcc \
        BUILD_DIR=release \
        CFLAGS="-Ofast -flto=auto -DNDEBUG -march=x86-64-v3 -Iinclude -pthread -static -static-libgcc" \
    && install -Dm755 release/Cepimetheus /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
