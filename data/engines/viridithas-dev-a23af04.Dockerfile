FROM rust:1.93.0-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:2a7d2ef636767d2cf56bbae12cec42e72845156fc2607bf92bdcfc9c98b03944 https://github.com/cosmobobak/viridithas-networks/releases/download/v111/hyperion-s2-b200.nnue.zst /build/viridithas.nnue.zst
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
