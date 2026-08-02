FROM golang:1.25.0-bookworm AS builder

RUN apt-get update \
    && apt-get install --no-install-recommends -y unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

ADD --checksum=sha256:44b4aed750b7f27a3b87ca9f1d564b5c4ca54ea64aac4e3940d574c279ceebb2 https://github.com/nescitus/Rodent-V/releases/download/Rodent_v_1_0/release.zip /tmp/rodent-release.zip
COPY . .

ENV CGO_ENABLED=0 \
    GOAMD64=v3

RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    unzip -j /tmp/rodent-release.zip release/nets/rodent_hm_512hl_1.bin -d nets \
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
