FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:edf1f4d219bf5c3136d60347ba294cc6fb93420c40e7ffe01568bda34ef0c061 https://raw.githubusercontent.com/GediminasMasaitis/chess-dot-cpp-networks/9080b0268db030887642e2c642d60974ec1f5bb9/default.nnue /build/src/ChessDotCpp/networks/default.nnue

COPY . .

WORKDIR /build/src/ChessDotCpp

RUN make \
        CXX=g++ \
        ARCH=avx2 \
        EVALFILE=networks/default.nnue \
        CFLAGS="-std=c++20 -O3 -Wall -Wextra -Wpedantic -march=x86-64-v3 -flto=auto -DNNUE=1 -DENABLE_INCBIN=1 -DENABLE_TABLEBASES=1 -DEVALFILE=\\\"networks/default.nnue\\\"" \
        LFLAGS="-pthread -static -flto=auto" \
        build \
    && install -Dm755 chessdotcpp-avx2 /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
