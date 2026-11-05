FROM golang:1.27.2-bookworm AS builder

WORKDIR /build
COPY . .

ENV CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOAMD64=v3
RUN go build -trimpath -ldflags="-s -w -X main.version=1.20" -o /opt/cope/engine ./cmd

FROM scratch
WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine
ENTRYPOINT ["./engine"]
