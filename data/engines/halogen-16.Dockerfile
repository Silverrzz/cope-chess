FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:150521a9957beb7fe90395dd5b134f75cde4e6dfc95e5d5fdee80031e7c7c9cb https://github.com/KierenP/Halogen-Networks/releases/download/150521a9/150521a9.nn /build/build/150521a9.nn
COPY . .

RUN make -C src -j4 release \
        CXX=g++ \
        CC=gcc \
        ARCH=avx2 \
        EXTRA_LDFLAGS="-static -static-libgcc -static-libstdc++" \
    && install -Dm755 bin/Halogen-avx2 /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
