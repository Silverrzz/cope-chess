FROM rust:1.99.0-bookworm AS builder

WORKDIR /build
COPY . .

RUN mkdir -p nets \
    && curl -fL https://github.com/Silverrzz/wakwak-nets/releases/download/shotis/shotis.nnue -o nets/wakwak.nnue \
    && echo "5a080efa1aa5381cc8efc7d2ca89f1f5ea20d3397ead83c3c7c12ef3b28e29cd  nets/wakwak.nnue" | sha256sum -c -

ENV RUSTFLAGS="-C target-cpu=x86-64-v3 -C target-feature=+crt-static"
RUN cargo build --release --locked --target x86_64-unknown-linux-gnu \
    && install -Dm755 target/x86_64-unknown-linux-gnu/release/wakwak /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch
WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine
ENTRYPOINT ["./engine"]
