FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:c3a7d03216238b9fdd299ec520e52b94ad394a0e7ddb84cab5ccb35978f50049 https://raw.githubusercontent.com/OmerFarukTutkun/DevreNets/main/devre-c3a7d0321623.bin /build/src/devre-c3a7d0321623.bin
COPY . .

RUN make -C src -j"$(nproc)" nopgo \
        build=avx2 \
        CXX=g++ \
    && install -Dm755 src/Devre /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
