FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:ed164cf2705c1f0b5d1be69422448ee999513ae4940a605a829fb4a868462f1c https://github.com/KierenP/Halogen-Networks/releases/download/ed164cf2/ed164cf2.nn /build/build/ed164cf2.nn
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
