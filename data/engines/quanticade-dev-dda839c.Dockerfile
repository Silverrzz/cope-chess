FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:bfbbe5474b2aef331a4c6d4213d777431e2812fe059f27c194cd12b2983a3bd4 https://media.githubusercontent.com/media/Quanticade/Networks/592d28e59ec9459bc7962396d16d1fd31a241e12/net62.nnue /build/net62.nnue
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
