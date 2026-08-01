FROM silkeh/clang:18-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:c0ba2688bf0a298636631a87ce705993807810b84087438461b6bab02abeb397 https://github.com/Bobingstern/tarnished-nets/releases/download/lichdragon-3/lichdragon-3.bin /build/lichdragon-3.bin
COPY . .

RUN make -j"$(nproc)" avx2 \
    && install -Dm755 tarnished /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
