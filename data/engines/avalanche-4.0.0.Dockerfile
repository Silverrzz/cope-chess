FROM gcc:14.3.0-bookworm AS builder

ADD --checksum=sha256:70e49664a74374b48b51e6f3fdfbf437f6395d42509050588bd49abe52ba3d00 https://ziglang.org/download/0.16.0/zig-x86_64-linux-0.16.0.tar.xz /tmp/zig.tar.xz

RUN mkdir -p /opt/zig \
    && tar -xJf /tmp/zig.tar.xz --strip-components=1 -C /opt/zig \
    && rm /tmp/zig.tar.xz

ENV PATH="/opt/zig:${PATH}"

WORKDIR /build
COPY . .

RUN --mount=type=cache,target=/root/.cache/zig \
    --mount=type=cache,target=/build/.zig-cache \
    zig build --release=fast \
        -Dtarget=x86_64-linux-musl \
        -Dcpu=x86_64_v3 \
        -Dtarget-name=engine \
    && install -Dm755 zig-out/bin/engine /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
