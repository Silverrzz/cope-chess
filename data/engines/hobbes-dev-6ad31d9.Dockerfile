FROM rust:1.89.0-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:804b057ea3918eea90c236aaa3b8f8c1235bced781dd6ce7ddbc1963477a4437 https://github.com/kelseyde/hobbes-networks/releases/download/hobbes-48/hobbes-48.nnue /build/hobbes.nnue
COPY . .

ENV CARGO_TERM_COLOR=never \
    RUSTFLAGS="-C target-cpu=x86-64-v3 -C link-arg=-Wl,--as-needed -C link-arg=-Wl,--gc-sections"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    cargo build --release --locked \
    && install -Dm755 target/release/hobbes-chess-engine /opt/cope/engine \
    && strip /opt/cope/engine

FROM debian:bookworm-slim

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
