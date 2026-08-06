FROM rust:1.97.1-bookworm AS builder

RUN rustup toolchain install nightly-2026-01-04 --profile minimal

WORKDIR /build

ADD --checksum=sha256:83b0c14fa69abd7a6e473290abb40a0c5315bb1fa6976d3e618e7743f6219312 https://github.com/Teccii/cherry-networks/releases/download/kiwi-v3/kiwi-v3.nnue /build/networks/default.nnue

COPY . .

ENV CARGO_TERM_COLOR=never \
    RUSTFLAGS="-C target-cpu=x86-64-v3"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    cargo +nightly-2026-01-04 build \
        --release \
        --package cherry \
        --bin cherry \
    && install -Dm755 target/release/cherry /opt/cope/engine \
    && strip /opt/cope/engine

FROM debian:bookworm-slim

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
