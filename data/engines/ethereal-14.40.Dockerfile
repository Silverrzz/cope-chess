FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build
COPY . .

RUN --mount=type=secret,id=engine_network,target=/run/secrets/engine_network,required=true \
    echo "c5e342ae3cb8f3e50be7fe64c9220e7a33d1a6e75aa3ca078e3ee48480d10db0  /run/secrets/engine_network" | sha256sum -c - \
    && make -C src avx2-popcnt \
        CC=clang \
        EVALFILE=/run/secrets/engine_network \
        EXE=ethereal \
    && install -Dm755 src/ethereal-avx2 /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
