FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .
ADD --checksum=sha256:0d9936472c089f8b19c52df5e1874b3944668f933f71d7beb3cfa270ce7f0b0e https://github.com/tgirolami09/Prune-nets/releases/download/nn-pulsar-dev-0d99364/pulsar.bin /build/core/pulsar.bin

RUN make -C core permute \
        ARCH=x86-64-v3 \
        OPT="-O3 -flto=auto -funroll-loops" \
        LDFLAGS="-static -flto=auto" \
        COM=3b9b84f \
        REC=v4.0.1 \
    && core/permute core/pulsar.bin core/model.bin \
    && make -C core -j"$(nproc)" all \
        EXE=prune \
        ARCH=x86-64-v3 \
        OPT="-O3 -flto=auto -funroll-loops" \
        LDFLAGS="-static -flto=auto" \
        COM=3b9b84f \
        REC=v4.0.1 \
    && install -Dm755 core/prune /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
