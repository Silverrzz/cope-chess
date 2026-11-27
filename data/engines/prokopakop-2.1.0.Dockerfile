FROM rust:1.97.1-bookworm AS builder

WORKDIR /build
COPY . .

RUN sed -i '/    let output = Command::new("git")/,/        .output();/c\    let output = Command::new("printf").arg("db34f06").output();' build.rs \
    && sed -i '/    env_logger::init();/a\    if std::env::args().nth(1).as_deref() == Some("bench") {\n        let mut controller = GameController::new();\n        controller.initialize();\n        controller.search(vec!["nodes".to_string(), "1000000".to_string()], true);\n        controller.wait_for_search().expect("benchmark search did not complete");\n        return;\n    }' src/main.rs \
    && test "$(grep -c '^[[:space:]]*search\.run()("printf").arg("db34f06")' build.rs \
    && grep -Fq 'Some("bench")' src/main.rs

ENV CARGO_TERM_COLOR=never \
    RUSTFLAGS="-C target-cpu=x86-64-v3"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    cargo build --release --locked --package prokopakop --bin prokopakop \
    && install -Dm755 target/release/prokopakop /opt/cope/engine \
    && strip /opt/cope/engine

FROM debian:bookworm-slim

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
 src/controller/controller.rs)" -eq 1 \
    && sed -i '/^[[:space:]]*search\.run()$/c\                      let result = search.run();\n                      if std::env::args_os().nth(1).as_deref() == Some(std::ffi::OsStr::new("bench")) {\n                          println!("{} nodes {} nps", search.stats.nodes, search.stats.get_nps());\n                      }\n                      result' src/controller/controller.rs \
    && grep -Fq 'println!("{} nodes {} nps", search.stats.nodes, search.stats.get_nps());' src/controller/controller.rs \
    && grep -Fq 'Command::new("printf").arg("db34f06")' build.rs \
    && grep -Fq 'Some("bench")' src/main.rs

ENV CARGO_TERM_COLOR=never \
    RUSTFLAGS="-C target-cpu=x86-64-v3"

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    cargo build --release --locked --package prokopakop --bin prokopakop \
    && install -Dm755 target/release/prokopakop /opt/cope/engine \
    && strip /opt/cope/engine

FROM debian:bookworm-slim

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
