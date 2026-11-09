FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .
RUN make -C src basic GIT_HEAD_COMMIT_ID_RAW=c735b8f 'CFLAGS=-std=gnu11 -O3 -flto=auto -march=x86-64-v2 -static $(GIT_HEAD_COMMIT_ID_DEF)' && install -Dm755 src/weiss /opt/cope/engine && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine
ENTRYPOINT ["./engine"]
