FROM rust:1.97.1-bookworm AS builder

WORKDIR /build
COPY . .
ADD --checksum=sha256:9da956a4c5359bc94236cd54f80237e6ed1ffe865bce92c411bb7f15eda84ac8 https://github.com/HasanFakih21/JustBot-Networks/releases/download/Networks/754a4ib-1024.nnue /build/model.nnue

ENV CARGO_TERM_COLOR=never \
    EVALFILE=/build/model.nnue \
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
