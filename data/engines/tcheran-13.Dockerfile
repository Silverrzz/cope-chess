FROM rust:1.97.1-alpine AS builder

WORKDIR /build
COPY . .
ADD --checksum=sha256:47dbf5d3da65ba9473e38ab3bc9e478e73ab84586af7e8af7b8fa9a913d86bb4 https://github.com/tcheran-chess/tcheran-networks/releases/download/networks/v24-47dbf5d3.nnue /build/data/v24-47dbf5d3.nnue

ENV CARGO_TERM_COLOR=never \
    CFLAGS_x86_64_unknown_linux_musl="-O3 -march=x86-64-v3" \
    RUSTFLAGS="-C target-cpu=x86-64-v3 -C target-feature=+crt-static"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    cargo build --release --locked --package engine --target x86_64-unknown-linux-musl \
    && install -Dm755 target/x86_64-unknown-linux-musl/release/tcheran /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
