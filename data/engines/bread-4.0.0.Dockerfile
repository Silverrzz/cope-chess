FROM gcc:14.3.0-bookworm AS builder

ADD --checksum=sha256:0dc2e9a6860f06bf10bd8fadc03e35d9eeb4df46e33763a7e480e987758f385c https://github.com/Kitware/CMake/releases/download/v3.31.12/cmake-3.31.12-linux-x86_64.tar.gz /tmp/cmake.tar.gz

RUN tar -xzf /tmp/cmake.tar.gz --strip-components=1 -C /usr/local \
    && rm /tmp/cmake.tar.gz

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
