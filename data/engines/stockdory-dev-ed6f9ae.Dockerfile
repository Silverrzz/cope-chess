FROM ubuntu:24.04 AS builder

ADD --checksum=sha256:8b2a587ffd672c4687e7581dad4b2f6c1bb2ad6b480cd9771ba2ff48e0b8c75d https://apt.llvm.org/llvm-snapshot.gpg.key /etc/apt/keyrings/llvm.asc

ARG LLVM_VERSION=1:22.1.8~++20260714014902+ca7933e47d3a-1~exp1~20260714135019.80
ENV DEBIAN_FRONTEND=noninteractive

RUN printf '%s\n' 'deb [signed-by=/etc/apt/keyrings/llvm.asc] https://apt.llvm.org/noble/ llvm-toolchain-noble-22 main' > /etc/apt/sources.list.d/llvm.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates cmake git ninja-build \
        "clang-22=${LLVM_VERSION}" "lld-22=${LLVM_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .
ADD --checksum=sha256:78ba32abdf798bc616bab7c73aac32a17bbd7b06ad9e26a6add69de8f3ae4791 https://github.com/cpm-cmake/CPM.cmake/releases/download/v0.40.8/CPM.cmake /build/build/cmake/CPM_0.40.8.cmake

RUN printf '%s\n' 'ed6f9aece2' > .stockdory-revision \
    && cmake -S . -B build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=clang++-22 \
        -DCMAKE_EXE_LINKER_FLAGS="--ld-path=/usr/bin/ld.lld-22 -static" \
        -DBUILD_NATIVE=OFF \
        -DARCHITECTURE=x86-64-v3 \
    && cmake --build build --target StockDory --parallel "$(nproc)" \
    && install -Dm755 build/StockDory /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
