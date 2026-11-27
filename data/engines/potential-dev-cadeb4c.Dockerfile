FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:043a7112c304f0dff5719f1be3ec8acb4314798a8e7069e9e40fb095643abc47 https://github.com/ProgramciDusunur/Potential-nets/releases/download/ti-2/potential-384hl-ti-2.bin /build/src/potential-384hl-ti-2.bin
COPY . .

RUN make -C src -j"$(nproc)" fast \
        build=x86-64-avx2 \
        COMMIT_SHA=cadeb4c \
        CLANG_CC=clang \
        LDFLAGS="-fuse-ld=lld -static" \
    && install -Dm755 src/Potential /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
