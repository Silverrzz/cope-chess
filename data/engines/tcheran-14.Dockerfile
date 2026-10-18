FROM rust:1.99.0-alpine AS builder

WORKDIR /build
COPY . .
ADD --checksum=sha256:fbb7eab600833c02363f2a6fcbcb0e1cef12389e507395381364d65bd59c430c https://github.com/tcheran-chess/tcheran-networks/releases/download/networks/v25-fbb7eab6.nnue /build/data/v25-fbb7eab6.nnue

ENV CARGO_TERM_COLOR=never \
    CFLAGS_x86_64_unknown_linux_musl="-O3 -march=x86-64-v3" \
    RUSTFLAGS="-C target-cpu=x86-64-v3 -C target-feature=+crt-static"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    cargo +1.99.0 build --release --locked --package engine --target x86_64-unknown-linux-musl \
    && install -Dm755 target/x86_64-unknown-linux-musl/release/tcheran /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
