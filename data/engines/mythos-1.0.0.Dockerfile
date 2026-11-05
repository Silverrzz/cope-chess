FROM rust:1.97.1-bookworm AS builder

WORKDIR /build
COPY . .

ENV CARGO_TERM_COLOR=never \
    RUSTFLAGS="-C target-cpu=x86-64-v3"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    echo "13371bb962b0b811f72eaad4ef7ad39e90cf48ed82ced500209c5415c94b21ae  nets/net.nnue" | sha256sum -c - \
    && cargo build --release --locked --package mythos --bin mythos \
    && install -Dm755 target/release/mythos /opt/cope/engine \
    && strip /opt/cope/engine

FROM debian:bookworm-slim

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
