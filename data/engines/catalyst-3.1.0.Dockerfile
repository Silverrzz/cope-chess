FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .
ADD --checksum=sha256:392db5efa7d463444f581014ff381c3965edc3652a5f92eca36597eb830bb335 https://github.com/AnanyTanwar/CatalystNet/releases/download/v2.0/catalyst-v2.nnue /build/catalyst-v2.nnue

RUN make -j"$(nproc)" linux-avx2 \
        CXX=g++ \
        LDFLAGS_LINUX="-pthread -flto=auto -static -static-libgcc -static-libstdc++ -Wl,--gc-sections -Wl,--no-as-needed" \
    && install -Dm755 bin/catalyst-linux-avx2 /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
