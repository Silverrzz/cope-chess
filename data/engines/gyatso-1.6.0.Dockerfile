FROM nimlang/nim:2.2.6-ubuntu-regular AS nim-toolchain

FROM silkeh/clang:21-trixie AS builder

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=nim-toolchain /nim/ /nim/

ENV PATH=/nim/bin:$PATH

WORKDIR /build
COPY . .

RUN --mount=type=cache,target=/root/.nimble \
    nimble install -y nimsimd@1.2.13 \
    && install -d /opt/cope \
    && nim c \
        -d:release \
        -d:danger \
        -d:simd \
        -d:avx2 \
        -d:bmi2 \
        --cc:clang \
        --parallelBuild:0 \
        --mm:arc \
        --define:useMalloc \
        --styleCheck:hint \
        --panics:on \
        --opt:speed \
        --passC:"-O3 -ffast-math -fstrict-aliasing -funroll-loops -fomit-frame-pointer -flto -fno-plt -march=x86-64-v3 -mtune=generic" \
        --passL:"-O3 -flto -fuse-ld=lld -static" \
        -o:/opt/cope/engine \
        Gyatso/src/main.nim \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
