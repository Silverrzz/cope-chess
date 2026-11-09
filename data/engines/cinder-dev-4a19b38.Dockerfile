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
    && install -Dm755 target/x86_64-unknown-linux-gnu/dist/cinder /opt/cope/cinder

FROM debian:bookworm-slim

WORKDIR /opt/cope
COPY --from=builder /opt/cope/cinder ./cinder
COPY --chmod=0755 <<'EOF' ./engine
#!/bin/sh
set -eu
engine="${0%/*}/cinder"
if [ "${1-}" = bench ]; then
    result=$(printf 'bench\n' | "$engine")
    printf '%s\n' "$result"
    printf '%s\n' "$result" | sed -n 's/^info .* nps \([1-9][0-9]*\)\( .*\)\{0,1\}$/Nodes per second: \1/p'
else
    exec "$engine" "$@"
fi
EOF

ENTRYPOINT ["./engine"]
