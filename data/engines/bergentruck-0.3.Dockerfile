FROM rust:1.97.1-bookworm AS builder

WORKDIR /build
COPY . .

ENV CARGO_TERM_COLOR=never \
    CARGO_PROFILE_RELEASE_DEBUG=0 \
    RUSTFLAGS="-C target-cpu=x86-64-v3 -C target-feature=+crt-static"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    cargo build --release --locked --jobs 4 \
        --target x86_64-unknown-linux-gnu --bin bergentruck-engine \
    && install -Dm755 target/x86_64-unknown-linux-gnu/release/bergentruck-engine /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
