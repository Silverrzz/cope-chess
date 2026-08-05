FROM gcc:14.3.0-bookworm AS builder

RUN apt-get update \
    && apt-get install --no-install-recommends -y cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .

RUN cmake -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_FLAGS_RELEASE="-O3 -DNDEBUG -march=x86-64-v3 -flto=auto" \
        -DCMAKE_EXE_LINKER_FLAGS_RELEASE="-flto=auto" \
        -DDEVELOPMENT=ON \
        -DINCLUDE_TRACING_MODULE=OFF \
        -DOPENBENCH_COMPLIANCE_MODE=ON \
    && cmake --build build --target illumina_cli_bmi2 --parallel "$(nproc)" \
    && install -Dm755 build/cli/illumina_bmi2 /opt/cope/engine \
    && strip /opt/cope/engine

FROM debian:bookworm-slim

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
