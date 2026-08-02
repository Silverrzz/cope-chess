FROM rust:1.89.0-bookworm AS rust-toolchain

FROM silkeh/clang:18-bookworm AS builder

COPY --from=rust-toolchain /usr/local/cargo/ /usr/local/cargo/
COPY --from=rust-toolchain /usr/local/rustup/ /usr/local/rustup/

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

ENV CARGO_HOME=/usr/local/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    PATH=/usr/local/cargo/bin:$PATH \
    LIBCLANG_PATH=/usr/lib/llvm-18/lib

WORKDIR /build
COPY . .

ENV CARGO_TERM_COLOR=never \
    RUSTFLAGS="-C target-cpu=x86-64-v3"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    cargo build --release --locked \
    && install -Dm755 target/release/reckless /opt/cope/engine \
    && strip /opt/cope/engine

FROM debian:bookworm-slim

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
