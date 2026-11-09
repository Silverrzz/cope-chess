FROM gcc:14.3.0-bookworm AS builder

RUN apt-get update \
    && apt-get install --no-install-recommends -y cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .
ADD --checksum=sha256:bc3df8d32b584f01e21f0f3f9515b3fc0f97fa0ca59b9b5991f333ed5b437b6b https://github.com/Witek902/Caissa-Nets/releases/download/eval-ml-11a-100B-spsa-transplant/eval-ml-11a-100B-spsa-transplant.pnn /build/data/neuralNets/eval-ml-11a-100B-spsa-transplant.pnn

RUN cmake -S . -B out -DCMAKE_BUILD_TYPE=Final -DTARGET_ARCH=x64-bmi2 -DUSE_NUMA=OFF \
        -DCMAKE_EXE_LINKER_FLAGS="-static -static-libgcc -static-libstdc++" \
    && cmake --build out --parallel "$(nproc)" \
    && install -Dm755 out/bin/caissa /opt/cope/engine \
    && strip /opt/cope/engine \
    && install -Dm644 data/neuralNets/eval-ml-11a-100B-spsa-transplant.pnn /opt/cope/eval-ml-11a-100B-spsa-transplant.pnn

FROM scratch
WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./
ENTRYPOINT ["./engine"]
