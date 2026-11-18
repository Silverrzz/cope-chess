FROM ubuntu:24.04 AS builder

RUN apt-get update \
    && apt-get install --no-install-recommends -y libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY LICENSE .
ADD --checksum=sha256:2b706718b0f5be7f815182b9bcb73cc6b0295d5e01aaeca7a84d84901cfec106 --chmod=755 https://github.com/TomaszJaworski777/Jackal/releases/download/2.0.0/jackal-2.0.0-x86-64-v3 /opt/cope/jackal
RUN install -Dm644 LICENSE /opt/cope/LICENSE \
    && mkdir -p /opt/cope/lib \
    && cd /usr/lib/x86_64-linux-gnu \
    && cp -L ld-linux-x86-64.so.2 libc.so.6 libm.so.6 libpthread.so.0 libdl.so.2 librt.so.1 libgcc_s.so.1 libstdc++.so.6 /opt/cope/lib/ \
    && install -Dm644 /usr/share/doc/libc6/copyright /opt/cope/licenses/glibc \
    && install -Dm644 /usr/share/doc/libstdc++6/copyright /opt/cope/licenses/gcc

COPY --chmod=755 <<'SH' /opt/cope/engine
#!/bin/sh
cd -- "${0%/*}" || exit 127
exec ./lib/ld-linux-x86-64.so.2 --library-path ./lib ./jackal "$@"
SH

FROM scratch
WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./
ENTRYPOINT ["./engine"]
