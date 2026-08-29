FROM gcc:14.3.0-bookworm AS builder

ADD --checksum=sha256:0dc2e9a6860f06bf10bd8fadc03e35d9eeb4df46e33763a7e480e987758f385c https://github.com/Kitware/CMake/releases/download/v3.31.12/cmake-3.31.12-linux-x86_64.tar.gz /tmp/cmake.tar.gz

RUN tar -xzf /tmp/cmake.tar.gz --strip-components=1 -C /usr/local \
    && rm /tmp/cmake.tar.gz

WORKDIR /build
COPY . .

RUN cmake -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=g++ \
        -DCMAKE_CXX_FLAGS_RELEASE="-O3 -DNDEBUG -march=x86-64-v3 -flto=auto -pthread" \
        -DCMAKE_EXE_LINKER_FLAGS_RELEASE="-flto=auto -pthread -static" \
    && cmake --build build --parallel "$(nproc)" --target surveyor \
    && install -Dm755 build/surveyor /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
