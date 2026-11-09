FROM gcc:14.3.0-bookworm AS builder

ADD --checksum=sha256:1cbe9df9f27e6b78d14ccbca43b6703a404ef79ef1c463de901d7f088d4e2026 https://ziglang.org/download/0.17.0/zig-x86_64-linux-0.17.0.tar.xz /tmp/zig.tar.xz

RUN mkdir -p /opt/zig \
    && tar -xJf /tmp/zig.tar.xz --strip-components=1 -C /opt/zig \
    && rm /tmp/zig.tar.xz

ENV PATH="/opt/zig:${PATH}"

WORKDIR /build
COPY . .
ADD --checksum=sha256:8b1d78b2908d330ace30ab69c3679a44db8176e0d6bf4116b60da8c383a4bc17 https://raw.githubusercontent.com/JonathanHallstrom/pawnocchio-nets/bbaf11347430bfedbf27461a1c3c2b360aa5e930/networks/pp_new2.nnue /build/pawnocchio-nets/networks/pp_new2.nnue

RUN --mount=type=cache,target=/root/.cache/zig \
    --mount=type=cache,target=/build/.zig-cache \
    zig build --release=fast \
        -Dtarget=x86_64-linux-musl \
        -Dcpu=x86_64_v3 \
        -Dlink_mode=static \
        -Dname=engine \
        -Dversion_string=dev-bd86090 \
    && mkdir -p /opt/cope \
    && cp zig-out/bin/engine /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
