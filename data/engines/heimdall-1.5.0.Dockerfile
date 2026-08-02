FROM nimlang/nim:2.2.6-ubuntu-regular AS nim-toolchain

FROM silkeh/clang:18-bookworm AS builder

COPY --from=nim-toolchain /nim/ /nim/

ENV PATH=/nim/bin:$PATH

WORKDIR /build
COPY . .
ADD --checksum=sha256:cc3d65d83383ed53848a16fdc7176e4dafe528c85f2722ae581f178c6e836381 https://git.nocturn9x.space/heimdall-engine/networks/media/commit/77efb7273d5758a725ea1cf2716b610cdddc757b/files/gramr.bin /build/networks/files/gramr.bin

RUN --mount=type=cache,target=/root/.nimble \
    nimble install -d -y \
    && make modern SKIP_DEPS=1 IS_RELEASE=1 \
    && install -Dm755 bin/heimdall /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
