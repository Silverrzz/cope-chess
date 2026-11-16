FROM golang:1.25.4-bookworm AS builder

WORKDIR /build
COPY . .

ENV CGO_ENABLED=0 \
    GOAMD64=v3

RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    make GIT_VERSION=v4.0 \
    && install -Dm755 chess3 /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
