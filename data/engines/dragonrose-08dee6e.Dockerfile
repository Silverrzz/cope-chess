FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .

RUN make \
        CXX=g++ \
        OPT_FLAGS="-O3 -ffast-math -march=x86-64-v3 -funroll-loops -flto=auto" \
        LDFLAGS="-static" \
    && install -Dm755 Dragonrose_Cpp /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
