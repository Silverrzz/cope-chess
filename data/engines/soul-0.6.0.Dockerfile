FROM rust:1.95.0-bookworm AS builder

RUN rustup toolchain install nightly-2026-06-18 --profile minimal

WORKDIR /build
COPY . .

ENV CARGO_TERM_COLOR=never \
    RUSTFLAGS="-C target-cpu=x86-64-v3"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    cargo +nightly-2026-06-18 build --release --locked --package soul \
    && install -Dm755 target/release/soul /opt/cope/engine \
    && strip /opt/cope/engine

FROM debian:bookworm-slim

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
