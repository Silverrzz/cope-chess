FROM rust:1.95.0-bookworm AS builder

RUN rustup toolchain install nightly-2026-11-01 --profile minimal

WORKDIR /build
COPY . .

ENV CARGO_TERM_COLOR=never \
    CARGO_NET_GIT_FETCH_WITH_CLI=true \
    RUSTFLAGS="-C target-cpu=x86-64-v3 -Z tune-cpu=znver3 -Z location-detail=none -C target-feature=+crt-static"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    cargo +nightly-2026-11-01 build --locked --profile dist --bin cinder --target x86_64-unknown-linux-gnu \
    && install -Dm755 target/x86_64-unknown-linux-gnu/dist/cinder /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
