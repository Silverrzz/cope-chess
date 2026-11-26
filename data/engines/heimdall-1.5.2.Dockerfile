FROM nimlang/nim:2.2.6-ubuntu-regular AS nim-toolchain

FROM silkeh/clang:18-bookworm AS builder

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates gcc git git-lfs zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=nim-toolchain /nim/ /nim/
ENV PATH=/nim/bin:$PATH
WORKDIR /build
COPY . .

RUN git init /tmp/heimdall-networks \
    && git -C /tmp/heimdall-networks remote add origin https://git.nocturn9x.space/heimdall-engine/networks \
    && git -C /tmp/heimdall-networks fetch --depth=1 origin 61eb4e4c7c861bfd6f5a5c3ffb8eab04942b2935 \
    && GIT_LFS_SKIP_SMUDGE=1 git -C /tmp/heimdall-networks checkout --detach FETCH_HEAD \
    && git -C /tmp/heimdall-networks lfs fetch --include=files/remiel.bin --exclude= origin HEAD \
    && git -C /tmp/heimdall-networks lfs checkout files/remiel.bin \
    && install -Dm644 /tmp/heimdall-networks/files/remiel.bin /build/networks/files/remiel.bin \
    && echo 'a8607157fbb8ac900ac7fe25d45d772509db9ecf3d9b5cb5000e5a581e1d0890  /build/networks/files/remiel.bin' | sha256sum --check

RUN --mount=type=cache,target=/root/.nimble \
    nimble install -d -y \
    && make avx2 SKIP_DEPS=1 IS_RELEASE=1 \
    && install -Dm755 bin/heimdall /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
