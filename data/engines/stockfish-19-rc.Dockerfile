FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:1a298aa575a085434d29027978dc36867fe9c5bcea9376654b7a8eba1e52dfc2 https://tests.stockfishchess.org/api/nn/nn-1a298aa575a0.nnue /build/src/nn-1a298aa575a0.nnue
COPY . .

RUN make -C src -j"$(nproc)" profile-build \
        ARCH=x86-64-bmi2 \
        COMP=gcc \
        ENV_CXXFLAGS="-march=x86-64-v3 -mtune=generic" \
        EXTRALDFLAGS="-static" \
    && install -Dm755 src/stockfish /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
