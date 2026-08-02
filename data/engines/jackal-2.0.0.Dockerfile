FROM rust:1.97.1-bookworm AS builder

WORKDIR /build
COPY . .
ADD --checksum=sha256:128c5d151821535499073ebd703126b04f3d6c67fb49ef3037563934634c8daa https://huggingface.co/datasets/Snekkers/networks/resolve/d35052f215351a7d06288fc5badc575a845e7ce5/v40004096001q.network /build/resources/networks/v40004096001q.network
ADD --checksum=sha256:8588bb9210289f6c30ac1ffe5535920e8ce59afc48e0125f9c74b79673fa437b https://huggingface.co/datasets/Snekkers/networks/resolve/d35052f215351a7d06288fc5badc575a845e7ce5/v40004096001qft3.network /build/resources/networks/v40004096001qft3.network
ADD --checksum=sha256:db6041b8e01de8e78019a754dece8a3c0bac038bdb297ffa222429d79663a9a5 https://huggingface.co/datasets/Snekkers/networks/resolve/d35052f215351a7d06288fc5badc575a845e7ce5/v40004096001qft5.network /build/resources/networks/v40004096001qft5.network
ADD --checksum=sha256:877566867abafd682b9d82a2400f5ebc6436d5efbf2ff7c6c2a0d7c81de0726d https://huggingface.co/datasets/Snekkers/networks/resolve/d35052f215351a7d06288fc5badc575a845e7ce5/p8008192009q.network /build/resources/networks/p8008192009q.network
ADD --checksum=sha256:90975c0ff87dab41fe7f2337a1f65ebc27b80222f280bed6f22feefd8536e424 https://huggingface.co/datasets/Snekkers/networks/resolve/d35052f215351a7d06288fc5badc575a845e7ce5/p8008192009qft3.network /build/resources/networks/p8008192009qft3.network
ADD --checksum=sha256:e88e8c306d02b53bfa1bcb0784166d9ad663755bdc8e43a017843430ff6cf928 https://huggingface.co/datasets/Snekkers/networks/resolve/d35052f215351a7d06288fc5badc575a845e7ce5/p8008192009qft4.network /build/resources/networks/p8008192009qft4.network
ADD --checksum=sha256:f0a28ff4e83e1cffe7faacde3c9cdeba8c134311c66404000c68960160be0a1c https://huggingface.co/datasets/Snekkers/networks/resolve/d35052f215351a7d06288fc5badc575a845e7ce5/p8008192009qft5.network /build/resources/networks/p8008192009qft5.network

ENV CARGO_TERM_COLOR=never \
    RUSTFLAGS="-C target-cpu=x86-64-v3"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    cargo build --release --package terminal \
    && install -Dm755 target/release/terminal /opt/cope/engine \
    && strip /opt/cope/engine

FROM debian:bookworm-slim

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
