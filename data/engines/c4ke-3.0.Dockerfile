FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .
RUN mkdir -p /opt/cope \
    && g++ -std=c++23 -DNDEBUG -O3 -march=x86-64-v3 -pthread -static src/main.cpp -o /opt/cope/engine \
    && install -Dm644 LICENSE /opt/cope/LICENSE \
    && strip /opt/cope/engine

FROM scratch
WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./
ENTRYPOINT ["./engine"]
