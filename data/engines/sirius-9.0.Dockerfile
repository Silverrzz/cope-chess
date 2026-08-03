FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build
COPY . .

RUN make -j"$(nproc)" \
        EXE=sirius \
        CXXFLAGS="-std=c++20 -O3 -flto -DNDEBUG -march=x86-64-v3" \
        LDFLAGS="-fuse-ld=lld -static" \
    && install -Dm755 sirius /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
