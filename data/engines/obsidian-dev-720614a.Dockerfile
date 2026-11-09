FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .
ADD --checksum=sha256:b7aba52339040fd65f66b18e8e84a079f8bad19746a1633a65818ccfec358b8f https://github.com/gab8192/Obsidian-nets/releases/download/nets/net89perm.bin /build/net89perm.bin

RUN sed -i '/^FLAGS = /s/$/ -static/' Makefile \
    && make -j"$(nproc)" nopgo build=avx2 EVALFILE=net89perm.bin \
    && install -Dm755 Obsidian /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
