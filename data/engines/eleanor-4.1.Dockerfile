FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build
COPY . .

RUN make ARCH_FLAGS="-march=x86-64-v3" EXE=eleanor \
    && install -Dm755 eleanor /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
