FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:f688b483513e801344f6e05046c8eea3d3650a8930ad4c2ccfafe862e8c515e0 https://git.nocturn9x.space/Quinniboi10/Chaos-Nets/raw/commit/7033b2b9799acb0793c2e49a7aa461bf7bddc8b8/Chaos_14.value /build/Chaos_14.value
ADD --checksum=sha256:496260a57b168765945a16d2e857a5166beb15347185481e9a19031bec557fde https://git.nocturn9x.space/Quinniboi10/Chaos-Nets/raw/commit/42d9fa5dd2f6ca6174da6a58c30977edbff20ba4/Chaos_13.policy /build/Chaos_13.policy

COPY . .

RUN make --jobs=4 \
        CXX=clang++ \
        CXXFLAGS="-O3 -fno-finite-math-only -funroll-loops -flto=thin -std=c++23 -DNDEBUG" \
        ARCHFLAGS="-march=x86-64-v3" \
        LINKFLAGS="-fuse-ld=lld -pthread -static" \
        VALUEFILE=Chaos_14.value \
        POLICYFILE=Chaos_13.policy \
    && install -Dm755 Chaos /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
