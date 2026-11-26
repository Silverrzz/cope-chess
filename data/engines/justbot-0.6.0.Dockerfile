FROM rust:1.97.1-bookworm AS builder

WORKDIR /build
COPY . .
ADD --checksum=sha256:971cc9fca1df282b0f27968eca5db540c0eae893aa33a1cf79566aa0b2bd636f https://github.com/HasanFakih21/JustBot-Networks/releases/download/Networks/1100a16ib-1024ml.nnue /build/model.nnue

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
