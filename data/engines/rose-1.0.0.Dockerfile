FROM silkeh/clang:21-trixie AS builder

WORKDIR /build
COPY . .
ADD --checksum=sha256:63b4ec93a24fb27371a8626eaaa9bf75ecb05e0101b56f97e91be2715c7c8a6d https://github.com/87flowers/rose-nets/releases/download/013-fourteenth/013-fourteenth.rosenet /build/networks/013-fourteenth.rosenet

RUN make -j"$(nproc)" rose \
        ARCH=x86-64-v3 \
        CXX=clang++ \
        GIT_COMMIT_DESC=1.0.0 \
        GIT_COMMIT_HASH=d33ed8f \
        LDFLAGS="-pthread -fuse-ld=lld -static -static-libgcc -static-libstdc++" \
    && install -Dm755 rose /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
