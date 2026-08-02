FROM rust:1.97.1-bookworm AS builder

WORKDIR /build
COPY . .
ADD --checksum=sha256:040d3959c43e55e710547ed5dd6e820b56182892acdc8c9181fbca019296e6a0 https://github.com/HasanFakih21/JustBot-Networks/releases/download/Networks/512-HL.nnue /build/model.nnue

RUN sed -i '/^    download_netowrk();$/d' build.rs

ENV CARGO_TERM_COLOR=never \
    RUSTFLAGS="-C target-cpu=x86-64-v3"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    cargo build --release --locked \
    && install -Dm755 target/release/justbot /opt/cope/engine \
    && strip /opt/cope/engine

FROM debian:bookworm-slim

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
