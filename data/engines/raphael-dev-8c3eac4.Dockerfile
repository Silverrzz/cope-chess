FROM gcc:14.3.0-bookworm AS builder
WORKDIR /build
COPY . .
ADD --checksum=sha256:745d9636272abd11673582627fdcf47098efccf7a6f43ff10313081aef696416 https://github.com/Orbital-Web/Raphael-Net/releases/download/yogsothoth_v4/yogsothoth_v4.nnue /build/yogsothoth_v4.nnue
RUN make -j4 CXX=g++ ARCH=avx2 LDFLAGS="-flto=auto -static -static-libgcc -static-libstdc++" uci \
    && install -Dm755 uci /opt/cope/engine \
    && strip /opt/cope/engine
FROM scratch
WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine
ENTRYPOINT ["./engine"]
