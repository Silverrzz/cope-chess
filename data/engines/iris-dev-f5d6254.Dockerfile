FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .
ADD --checksum=sha256:7be7c4729fcc0f1cdbad5e62e5b335955588006dee42aec8c68e45a237ed9044 https://github.com/citrus610/iris-net/releases/download/lavender/lavender.bin lavender.bin
RUN make v3 STATIC=-static \
    && install -Dm755 iris_x86-64-v3 /opt/cope/engine \
    && install -Dm644 LICENSE /opt/cope/LICENSE \
    && strip /opt/cope/engine

FROM scratch
WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./
ENTRYPOINT ["./engine"]
