FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .

RUN install -d /opt/cope \
    && g++ \
        -std=c++17 \
        -O3 \
        -DNDEBUG \
        -march=x86-64-v3 \
        -flto=auto \
        -pthread \
        -Isrc/fathom \
        -Isrc/incbin \
        -DUSE_NN \
        -DNN_FILE=\"nn/nn_1.9k_e500_l400_d500.nn\" \
        -DUSE_POPCNT \
        -DUSE_PEXT \
        -DIS_TUNING \
        src/*.cpp \
        src/fathom/tbprobe.c \
        -static \
        -static-libgcc \
        -static-libstdc++ \
        -o /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
