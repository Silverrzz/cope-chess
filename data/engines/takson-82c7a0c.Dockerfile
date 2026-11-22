FROM rust:1.95.0-bookworm AS builder

WORKDIR /build
COPY . .
ADD --checksum=sha256:2e4ca7d4344896983758e9e7c05efec7b48e372eee38e60d50dd23dc7fe5ec8d https://github.com/Ciekce/takson-nets/releases/download/value001/value001.val /nets/value001.val
ADD --checksum=sha256:a75df524c0a695a8241b36af50405b39727ba82d1cdc607cc498bb6c427fac3c https://github.com/Ciekce/takson-nets/releases/download/policy004/policy004.pol /nets/policy004.pol
ENV CARGO_TERM_COLOR=never RUSTFLAGS="-C target-cpu=x86-64-v3 -C target-feature=+crt-static"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/build/target \
    sed -i '/    if let Ok(p) = env::var("EVALFILE") {/i\    if let (Ok(value), Ok(policy)) = (env::var("EVALFILE"), env::var("POLICYFILE")) {\n        println!("cargo::rustc-env=EVALFILE={value}");\n        println!("cargo::rustc-env=POLICYFILE={policy}");\n        return;\n    }' build.rs \
    && sed -i 's/mask.isolate_lowest_one()/(mask \& mask.wrapping_neg())/' src/hits/common.rs \
    && sed -i 's/pattern.isolate_lowest_one()/(pattern \& pattern.wrapping_neg())/' src/movegen.rs \
    && EVALFILE=/nets/value001.val POLICYFILE=/nets/policy004.pol cargo build --locked --release --target x86_64-unknown-linux-gnu \
    && install -Dm755 target/x86_64-unknown-linux-gnu/release/takson /opt/cope/takson

FROM debian:bookworm-slim
WORKDIR /opt/cope
COPY --from=builder /opt/cope/takson ./takson
COPY --chmod=0755 <<'EOF' ./engine
#!/bin/sh
set -eu
engine="${0%/*}/takson"
if [ "${1-}" != bench ]; then
    exec "$engine" "$@"
fi
output=$("$engine" bench)
printf '%s\n' "$output"
printf '%s\n' "$output" | awk '
    $2=="nodes" && $4=="nps" { nodes=$1; nps=$3 }
    END { if (nodes<=0 || nps<=0) exit 1; printf "Nodes searched: %d\nNodes per second: %d\n", nodes, nps }
'
EOF
ENTRYPOINT ["./engine"]
