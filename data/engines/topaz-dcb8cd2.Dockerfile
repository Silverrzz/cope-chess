FROM rust:1.95.0-bookworm AS builder

WORKDIR /build
COPY . .
ADD --checksum=sha256:55a44f5850a40458f94d775a8c4c871b5462ebdbdb98d1276e3f544a316c3a9d https://drive.usercontent.google.com/download?id=1g5rChfkfhaeAG_PcJx2CX-eqmLKlyj6w&export=download&confirm=t /build/src/quantised.bin
ENV CARGO_TERM_COLOR=never RUSTFLAGS="-C target-cpu=x86-64-v3 -C target-feature=+crt-static"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/build/target \
    sed -i 's/bits.lowest_one().unwrap()/bits.trailing_zeros()/' src/eval/incremental.rs \
    && cargo build --locked --release --bin topaz --target x86_64-unknown-linux-gnu \
    && install -Dm755 target/x86_64-unknown-linux-gnu/release/topaz /opt/cope/topaz

FROM debian:bookworm-slim
WORKDIR /opt/cope
COPY --from=builder /opt/cope/topaz ./topaz
COPY --from=builder /build/LICENSE ./LICENSE
COPY --chmod=0755 <<'EOF' ./engine
#!/bin/bash
set -eu
engine="${0%/*}/topaz"
if [[ ${1-} != bench ]]; then
    exec "$engine" "$@"
fi
coproc SEARCH { exec "$engine"; }
pid=$SEARCH_PID
exec {input}>&"${SEARCH[1]}" {output}<&"${SEARCH[0]}"
trap 'kill "$pid" 2>/dev/null || true' EXIT
printf '%s\n' tei 'setoption name HalfKomi value 4' 'setoption name Hash value 64' 'teinewgame 6' 'position startpos moves a1 f6 c3 d4 Sc4 Cd3' 'go nodes 1000000' >&"$input"
result=$(while IFS= read -r line; do
    printf '%s\n' "$line"
    case "$line" in bestmove\ *) break ;; esac
done <&"$output")
printf '%s\n' quit >&"$input"
wait "$pid"
trap - EXIT
printf '%s\n' "$result"
printf '%s\n' "$result" | awk '
    /^info / { for (i=2;i<NF;i++) { if ($i=="nodes") nodes=$(i+1); if ($i=="nps") nps=$(i+1) } }
    /^bestmove / { done=1; exit }
    END { if (!done || nodes<=0 || nps<=0) exit 1; printf "Nodes searched: %d\nNodes per second: %d\n", nodes, nps }
'
EOF
ENTRYPOINT ["./engine"]
