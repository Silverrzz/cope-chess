FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build
COPY . .

RUN make \
        CXX=clang++ \
        CXXFLAGS="-O3 -Isrc -flto=thin -std=c++20 -march=x86-64-v3 -Wall -Wextra -pedantic -DNDEBUG -pthread" \
        LDFLAGS="-fuse-ld=lld -static" \
    && install -Dm755 midnight /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
