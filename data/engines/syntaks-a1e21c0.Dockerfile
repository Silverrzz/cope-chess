FROM rust:1.95.0-bookworm AS builder

WORKDIR /build
COPY . .
ADD --checksum=sha256:329f2e6d70250f0f632fd90b387a5827bf3269aa6d8f1585c46f0af244cb75c0 https://github.com/Ciekce/syntaks-nets/releases/download/cnet023/cnet023.nnue /nets/cnet023.nnue
ENV CARGO_TERM_COLOR=never RUSTFLAGS="-C target-cpu=x86-64-v3 -C target-feature=+crt-static"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/build/target \
    EVALFILE=/nets/cnet023.nnue cargo build --locked --release --target x86_64-unknown-linux-gnu \
    && install -Dm755 target/x86_64-unknown-linux-gnu/release/syntaks /opt/cope/syntaks

FROM debian:bookworm-slim
WORKDIR /opt/cope
COPY --from=builder /opt/cope/syntaks ./syntaks
COPY --from=builder /build/LICENSE.txt ./LICENSE.txt
COPY --chmod=0755 <<'EOF' ./engine
#!/bin/sh
set -eu
engine="${0%/*}/syntaks"
if [ "${1-}" != bench ]; then
    exec "$engine" "$@"
fi
output=$(printf '%s\n' tei 'setoption name RandomMoveChance value 0' 'setoption name HalfKomi value 4' 'setoption name Hash value 64' 'teinewgame 6' 'position startpos moves a1 f6 c3 d4 Sc4 Cd3' 'go nodes 5000000' wait quit | "$engine")
printf '%s\n' "$output"
printf '%s\n' "$output" | awk '
    /^info / { for (i=2;i<NF;i++) { if ($i=="nodes") nodes=$(i+1); if ($i=="nps") nps=$(i+1) } }
    /^bestmove / { done=1 }
    END { if (!done || nodes<=0 || nps<=0) exit 1; printf "Nodes searched: %d\nNodes per second: %d\n", nodes, nps }
'
EOF
ENTRYPOINT ["./engine"]
