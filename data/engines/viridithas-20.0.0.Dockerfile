FROM rust:1.93.0-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:066c172e22c25ee3d3b4084ad44ff761426f906a9c536cabd0127d17413d0585 https://github.com/cosmobobak/viridithas-networks/releases/download/v109/sandhi-s2-b200.nnue.zst /build/viridithas.nnue.zst
COPY . .

ENV CARGO_TERM_COLOR=never \
    RUSTFLAGS="-C target-cpu=x86-64-v3"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    cargo build --release --features syzygy \
    && install -Dm755 target/release/viridithas /opt/cope/engine \
    && strip /opt/cope/engine

FROM debian:bookworm-slim

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
