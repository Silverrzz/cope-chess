FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .

RUN sed -i 's/int(abs(hash%ht_size))/int(hash%ht_size)/' engine.h \
    && install -d /opt/cope \
    && g++ UCI_wrapper.cpp \
        -o /opt/cope/engine \
        -std=c++17 \
        -O3 \
        -march=x86-64-v3 \
        -mtune=generic \
        -flto=auto \
        -static \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
