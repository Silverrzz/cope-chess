FROM rust:1.95.0-bookworm AS builder

WORKDIR /build
COPY . .
ENV CARGO_TERM_COLOR=never RUSTFLAGS="-C target-cpu=x86-64-v3 -C target-feature=+crt-static"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/build/target \
    sed -i 's/assert_eq!(cmd.next().unwrap(), "moves");/if let Some(arg) = cmd.next() { assert_eq!(arg, "moves"); }/' src/tei.rs \
    && cargo build --locked --release --no-default-features --features 6 --target x86_64-unknown-linux-gnu \
    && install -Dm755 target/x86_64-unknown-linux-gnu/release/cataklysm /opt/cope/cataklysm

FROM debian:bookworm-slim
WORKDIR /opt/cope
COPY --from=builder /opt/cope/cataklysm ./cataklysm
COPY --chmod=0755 <<'EOF' ./engine
#!/bin/bash
set -eu
engine="${0%/*}/cataklysm"
if [[ ${1-} != bench ]]; then
    exec "$engine" tei "$@"
fi
coproc SEARCH { exec "$engine" tei; }
pid=$SEARCH_PID
exec {input}>&"${SEARCH[1]}" {output}<&"${SEARCH[0]}"
trap 'kill "$pid" 2>/dev/null || true' EXIT
printf '%s\n' tei 'setoption name HalfKomi value 4' 'teinewgame 6' 'position startpos moves a1 f6 c3 d4 Sc4 Cd3' 'go movetime 1000' >&"$input"
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
