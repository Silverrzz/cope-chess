FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .

RUN make -C src -j"$(nproc)" make \
        CXX=g++ \
        build_flag=avx2 \
        LIBS="-lpthread -static -static-libgcc -static-libstdc++" \
    && install -Dm755 src/Clover.9.1-avx2 /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
