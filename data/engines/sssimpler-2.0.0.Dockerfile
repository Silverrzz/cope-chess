FROM silkeh/clang:21-trixie AS builder

WORKDIR /build
COPY . .

RUN install -d /opt/cope \
    && clang++ \
        -std=c++2a \
        -O3 \
        -DNDEBUG \
        -march=x86-64-v3 \
        -mtune=generic \
        -flto \
        -pthread \
        -fno-exceptions \
        -fno-rtti \
        -ffast-math \
        -funroll-loops \
        -fuse-ld=lld \
        -static \
        -static-libgcc \
        -static-libstdc++ \
        code/main.cpp \
        -o /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
