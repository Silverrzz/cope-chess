FROM debian:bookworm-slim AS builder

WORKDIR /build
COPY LICENSE .
ADD --checksum=sha256:2b706718b0f5be7f815182b9bcb73cc6b0295d5e01aaeca7a84d84901cfec106 --chmod=755 https://github.com/TomaszJaworski777/Jackal/releases/download/2.0.0/jackal-2.0.0-x86-64-v3 /opt/cope/engine
RUN install -Dm644 LICENSE /opt/cope/LICENSE

FROM scratch
WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./
ENTRYPOINT ["./engine"]
