FROM rust:1.99.0-bookworm AS builder

RUN apt-get update \
    && apt-get install --no-install-recommends -y nasm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .
RUN sed -i 's/version = "0.2.140"/version = "=0.2.140"/' stro/Cargo.toml \
    && sed -i '0,/search.search(false, depth);/s//search.search_asm(false, depth);/' stro/src/search.rs \
    && RUSTFLAGS="-C target-cpu=x86-64-v3 -C target-feature=+crt-static -C relocation-model=static -C link-arg=-no-pie" \
       cargo build --manifest-path stro/Cargo.toml --release --target x86_64-unknown-linux-gnu \
    && install -Dm755 stro/target/x86_64-unknown-linux-gnu/release/stro /opt/cope/engine \
    && install -Dm644 LICENSE /opt/cope/LICENSE \
    && strip /opt/cope/engine

FROM scratch
WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./
ENTRYPOINT ["./engine"]
