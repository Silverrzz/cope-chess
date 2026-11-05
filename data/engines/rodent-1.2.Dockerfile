FROM golang:1.25.0-bookworm AS builder

WORKDIR /build
COPY go.mod go.sum default.pgo *.go *.s ./
COPY nets/rodent_4kb_768hl_8ob_v2.bin ./nets/rodent_4kb_768hl_8ob_v2.bin

ENV CGO_ENABLED=0 \
    GOAMD64=v3

RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    echo "c35a1abc1b8c1cb1d5f4221454d494c1a6da1ed9088fd51ab27038bfa74b5053  nets/rodent_4kb_768hl_8ob_v2.bin" | sha256sum -c - \
    && go build \
        -buildvcs=false \
        -mod=readonly \
        -trimpath \
        -ldflags="-s -w" \
        -o /opt/cope/engine \
        .

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
