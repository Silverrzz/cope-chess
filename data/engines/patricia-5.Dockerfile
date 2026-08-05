FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build
COPY . .

RUN make -C engine \
        CXX=clang++ \
        CXXFLAGS="-O3 -flto=full -march=x86-64-v3 -std=c++20 -ffast-math -pthread -static" \
        LINKER="-lm" \
    && install -Dm755 engine/patricia /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
