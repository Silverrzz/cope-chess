FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:b008f7ba0163ee7f755e68c0b3cda5b105f5b86362c10a5572213a6d4583b600 https://github.com/ProgramciDusunur/Potential-nets/releases/download/1024hl-2/potential-1024hl-480sb.bin /build/src/potential-1024hl-480sb.bin
COPY . .

RUN make -C src -j"$(nproc)" fast \
        build=x86-64-avx2 \
        COMMIT_SHA=b5a9c65 \
        CLANG_CC=clang \
        LDFLAGS="-fuse-ld=lld -static" \
    && install -Dm755 src/Potential /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
