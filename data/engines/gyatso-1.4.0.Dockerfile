FROM nimlang/nim:2.2.6-alpine-regular AS builder

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
        --cc:gcc \
        --parallelBuild:0 \
        --mm:arc \
        --define:useMalloc \
        --styleCheck:hint \
        --panics:on \
        --opt:speed \
        --passC:"-O3 -ffast-math -fstrict-aliasing -funroll-loops -fomit-frame-pointer -flto -fno-plt -march=x86-64-v3" \
        --passL:"-O3 -flto -static" \
        -o:/opt/cope/engine \
        Gyatso/src/main.nim \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
