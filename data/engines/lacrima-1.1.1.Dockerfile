FROM golang:1.26.3-bookworm AS builder

WORKDIR /build
COPY . .

ENV CGO_ENABLED=0 \
    GOAMD64=v3

RUN --mount=type=cache,target=/root/.cache/go-build \
    install -d /opt/cope \
    && go build -trimpath -ldflags="-s -w" -o /opt/cope/engine ./cmd/lacrima

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
