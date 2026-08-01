FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build
COPY . .

RUN install -d /opt/cope \
    && clang++ \
        -std=c++20 \
        -O3 \
        -DNDEBUG \
        -march=x86-64-v3 \
        -flto=thin \
        -fuse-ld=lld \
        -pthread \
        -static \
        -static-libgcc \
        -static-libstdc++ \
        main.cpp \
        -o /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
