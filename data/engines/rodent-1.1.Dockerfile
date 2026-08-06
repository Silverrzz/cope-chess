FROM golang:1.25.0-bookworm AS builder

WORKDIR /build
COPY go.mod go.sum *.go *.s ./
COPY nets/rodent_hm_512hl_1.bin ./nets/rodent_hm_512hl_1.bin

ENV CGO_ENABLED=0 \
    GOAMD64=v3

RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    echo "cd8acd1b9bde058e7c7168a6a31440438034ea2265f4871834775baab2909e1e  nets/rodent_hm_512hl_1.bin" | sha256sum -c - \
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
