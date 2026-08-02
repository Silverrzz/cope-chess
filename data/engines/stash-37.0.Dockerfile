FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .

RUN make -C src -j"$(nproc)" \
        ARCH=x86-64-bmi2 \
        CC=gcc \
        CFLAGS="-O3 -flto=auto -DNDEBUG -march=x86-64-v3 -mtune=generic" \
        LDFLAGS="-static -flto=auto" \
    && install -Dm755 src/stash /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
