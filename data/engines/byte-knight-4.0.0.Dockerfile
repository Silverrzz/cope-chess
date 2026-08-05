FROM rust:1.97.1-bookworm AS builder

WORKDIR /build
COPY . .

ENV CARGO_TERM_COLOR=never \
    RUSTFLAGS="-C target-cpu=x86-64-v3"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    cargo build --release --locked --package byte-knight --bin byte-knight \
    && install -Dm755 target/release/byte-knight /opt/cope/engine \
    && strip /opt/cope/engine

FROM debian:bookworm-slim

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
