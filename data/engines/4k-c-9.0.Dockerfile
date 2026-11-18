FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .
RUN mkdir -p /opt/cope \
    && gcc -std=gnu2x -DNDEBUG -DFULL -O3 -march=x86-64-v3 -pthread -static 4k.c -o /opt/cope/engine \
    && install -Dm644 LICENSE /opt/cope/LICENSE \
    && install -Dm644 THIRD_PARTY_LICENSES /opt/cope/THIRD_PARTY_LICENSES \
    && strip /opt/cope/engine

FROM scratch
WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./
ENTRYPOINT ["./engine"]
