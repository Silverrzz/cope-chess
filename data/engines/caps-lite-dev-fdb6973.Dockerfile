FROM rust:1.99.0-bookworm AS builder

WORKDIR /build
COPY . .

ENV RUSTUP_TOOLCHAIN=1.99.0
ENV RUSTFLAGS="-C target-cpu=x86-64-v3 -C target-feature=+crt-static"
RUN cargo build --release --locked --target x86_64-unknown-linux-gnu \
    --package motors --bin motors --no-default-features --features=caps,unsafe \
    && install -Dm755 target/x86_64-unknown-linux-gnu/release/motors /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch
WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine
ENTRYPOINT ["./engine"]
