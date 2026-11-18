FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .
RUN make -C src -j2 build=x86-64-bmi2 FLAGS='-pthread -static' \
    && install -Dm755 src/smallbrain-x86-64-bmi2 /opt/cope/engine \
    && install -Dm644 LICENSE /opt/cope/LICENSE \
    && strip /opt/cope/engine

FROM scratch
WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./
ENTRYPOINT ["./engine"]
