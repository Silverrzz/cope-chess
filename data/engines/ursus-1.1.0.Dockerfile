FROM gcc:14.3.0-bookworm AS builder

ADD --checksum=sha256:c61c5da6edeea14ca51ecd5e4520c6f4189ef5250383db33d01848293bfafe05 https://ziglang.org/download/0.15.1/zig-x86_64-linux-0.15.1.tar.xz /tmp/zig.tar.xz

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/zig \
    && tar -xJf /tmp/zig.tar.xz --strip-components=1 -C /opt/zig \
    && rm /tmp/zig.tar.xz

ENV PATH="/opt/zig:${PATH}"

WORKDIR /build
COPY . .

RUN --mount=type=cache,target=/root/.cache/zig \
    --mount=type=cache,target=/build/.zig-cache \
    zig build \
        -Doptimize=ReleaseFast \
        -Dtarget=x86_64-linux-musl \
        -Dcpu=x86_64_v3 \
    && install -Dm755 zig-out/bin/Ursus /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
