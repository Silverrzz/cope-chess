FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:35b74cc524be60c2debf103facdcb75055bf1c217a0afb41502f535de725b7d5 https://git.nocturn9x.space/Quinniboi10/Prelude-Nets/raw/commit/c9b62def4f9b7ed300530b6885dc74f1f6193472/Prelude_09.nnue /build/Prelude_09.nnue

COPY . .

RUN make --jobs=4 \
        CXX=clang++ \
        CXXFLAGS="-O3 -fno-finite-math-only -funroll-loops -flto=thin -std=c++20 -DNDEBUG" \
        ARCHFLAGS="-march=x86-64-v3" \
        LINKFLAGS="-fuse-ld=lld -pthread -static" \
        EVALFILE=Prelude_09.nnue \
    && install -Dm755 Prelude /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
