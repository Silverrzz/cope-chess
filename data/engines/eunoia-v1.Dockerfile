FROM golang:1.26.5-bookworm AS builder

WORKDIR /build
COPY . .

RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOAMD64=v3 \
    go build -trimpath -buildvcs=false -ldflags="-s -w" -o /opt/cope/engine ./cmd/eunoia

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]

