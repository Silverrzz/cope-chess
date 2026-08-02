FROM gcc:14.3.0-bookworm AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .

RUN sed -i 's/add_compile_options(-march=native)/add_compile_options(-march=x86-64-v3)/' CMakeLists.txt \
    && cmake -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=g++ \
        -DCMAKE_EXE_LINKER_FLAGS="-static" \
    && cmake --build build --parallel "$(nproc)" --target bread_engine \
    && install -Dm755 build/bread_engine_4.0.0 /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
