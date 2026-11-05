FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .

RUN make -j"$(nproc)" \
        CC=gcc \
        ARCH=x86-64-v3 \
        CFLAGS="-O3 -Wall -Wextra -pedantic -std=gnu99 -DNDEBUG -march=x86-64-v3 -mtune=generic -flto=auto" \
        LDFLAGS="-static -flto=auto -lm" \
    && install -Dm755 bin/chal /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
