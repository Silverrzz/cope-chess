FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .

RUN make -C Renegade -j"$(nproc)" \
        build=x86-64-bmi2 \
        CXX=g++ \
        LDFLAGS="-pthread -static -static-libgcc -static-libstdc++" \
    && install -Dm755 Renegade/Renegade /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
