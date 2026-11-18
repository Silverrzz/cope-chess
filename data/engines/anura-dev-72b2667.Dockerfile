FROM rust:1.99.0-bookworm AS builder

WORKDIR /build
COPY . .
ADD --checksum=sha256:e351f42c22d18b10f16028255c592f52e7de34623cc2ace9bd4b5f28dc3010f1 https://github.com/Vast342/anura-nets/releases/download/avn_015_ti/avn_015_ti.vn src/nets/avn.vn
ADD --checksum=sha256:82818917df542f51306feea27c9ea8dad249fc4157588f5cd3f16737cebbea6e https://github.com/Vast342/anura-nets/releases/download/apn_019_1024/apn_019_1024.pn src/nets/apn.pn
RUN RUSTFLAGS="-C target-cpu=x86-64-v3 -C target-feature=+crt-static" \
    cargo build --locked --release --target x86_64-unknown-linux-gnu \
    && install -Dm755 target/x86_64-unknown-linux-gnu/release/anura /opt/cope/engine \
    && install -Dm644 LICENSE /opt/cope/LICENSE \
    && strip /opt/cope/engine

FROM scratch
WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./
ENTRYPOINT ["./engine"]
