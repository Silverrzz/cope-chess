FROM golang:1.25.0-bookworm AS builder

WORKDIR /build
COPY go.mod go.sum default.pgo *.go *.s ./
COPY nets/rodent_8kb_512pw_multilayer_v8.bin ./nets/rodent_8kb_512pw_multilayer_v8.bin

ENV CGO_ENABLED=0 \
    GOAMD64=v3

RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    echo "4ba5791a15592a0927b994643102560bc65d2a916d64a1effb1f890cea7b2252  nets/rodent_8kb_512pw_multilayer_v8.bin" | sha256sum -c - \
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
