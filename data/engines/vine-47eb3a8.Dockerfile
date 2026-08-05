FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build
COPY . .

ADD --checksum=sha256:2676733daf967194bd5a6af4bef8d8ee81403dc3f5b8528844fa5118d56fa2d6 https://raw.githubusercontent.com/vine-chess/vine-networks/128d919abc76c3068caeccec4aca59776fa976d9/value/net58.vn /build/net58.vn
ADD --checksum=sha256:2c72673db6ad2aa2570869e24fc611c89a6e7e86d601f0ae3112a75c4415a03f https://raw.githubusercontent.com/vine-chess/vine-networks/128d919abc76c3068caeccec4aca59776fa976d9/policy/net23.pn /build/net23.pn

RUN make \
        CC=clang \
        CXX=clang++ \
        build=avx2 \
        EXTRA_FLAGS="-include bit -march=x86-64-v3 -static -fuse-ld=lld" \
    && install -Dm755 vine /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
