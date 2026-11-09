FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .
ADD --checksum=sha256:9bd0602019cd6a197f783073b88ec4e467499f5e034ce9a48b69ec52c1e61af9 https://github.com/PGG106/Alexandria-networks/releases/download/net16/nn.net /build/nn.net

RUN make -j"$(nproc)" build=x86-64-avx2 FLAGS="-pthread -static" \
    && install -Dm755 Alexandria /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
