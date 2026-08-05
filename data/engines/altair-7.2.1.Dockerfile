FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build
COPY . .

RUN make \
        CXX=clang++ \
        CXXFLAGS="-O3 -std=c++20 -march=x86-64-v3 -Wall -Wextra -pedantic -DNDEBUG -flto=thin -fuse-ld=lld -pthread -static -DALTAIR_SRC_DIR=\\\"src/\\\"" \
    && install -Dm755 Altair /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
