FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:530d5ba0fff730bb15fa5590d622e73825f36c19c6b7adb02f01273334650005 https://media.githubusercontent.com/media/Quanticade/Networks/e54850b5216c3403fce5b0e97b335df634ab1bec/net44.nnue /build/net44.nnue
COPY . .

RUN make -j"$(nproc)" \
        CC=clang \
        build=x86-64-avx2 \
        FLAGS="-pthread -lm -static" \
    && install -Dm755 Quanticade /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
