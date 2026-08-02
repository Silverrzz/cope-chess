FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .

RUN make -j"$(nproc)" \
        CC=gcc \
        CFLAGS="-std=c2x -D_POSIX_C_SOURCE=200809L -Wall -Wextra -Wshadow -Wpedantic -O3 -DNDEBUG -flto=auto -march=x86-64-v3 -mtune=generic" \
        LDFLAGS="-flto=auto -static" \
    && install -Dm755 crustik /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
