FROM rust:1.95.0-alpine AS builder

WORKDIR /build
COPY . .
ADD --checksum=sha256:ae1c7ec7f901b5e032538a85b4ddf715c5afe6d5a5f787c2f2f2132b5eda1da1 https://github.com/Sp00ph/icarus-nets/releases/download/glide-v22/glide-v22.nnue /build/nets/icarus.nnue

ENV CARGO_TERM_COLOR=never \
    ICARUS_RELEASE=1 \
    RUSTFLAGS="-C target-cpu=x86-64-v3 -C target-feature=+crt-static"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    cargo build --release --locked --package icarus --features use-bmi2 --target x86_64-unknown-linux-musl \
    && install -Dm755 target/x86_64-unknown-linux-musl/release/icarus /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
