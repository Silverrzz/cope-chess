FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .
RUN printf '%s  %s\n' '36d69f04682bf7c515abd67e159064d75771aaf885d2ae96c4366b229ef8fbb7' 'src/kuma.nnue' | sha256sum --check \
    && mkdir -p /opt/cope \
    && cd src \
    && g++ -std=c++20 -DNDEBUG -O3 -march=x86-64-v3 -pthread -static *.cpp -o /opt/cope/engine \
    && install -Dm644 ../LICENSE /opt/cope/LICENSE \
    && install -Dm644 unlicence.txt /opt/cope/INCBIN-LICENSE \
    && strip /opt/cope/engine

FROM scratch
WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./
ENTRYPOINT ["./engine"]
