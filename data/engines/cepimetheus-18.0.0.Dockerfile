FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .

RUN sed -i 's/%llu nodes %llu nps %lld ms/%llu nodes %llu nps, %lld ms/' src/uci.c \
    && make -j"$(nproc)" \
        CC=gcc \
        BUILD_DIR=release \
        CFLAGS='$(RELEASE_FLAGS) -DNDEBUG -DNO_PEXT -march=x86-64-v3 -static -static-libgcc' \
    && install -Dm755 release/Cepimetheus /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
