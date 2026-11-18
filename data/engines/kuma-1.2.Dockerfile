FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .
RUN printf '%s  %s\n' '36d69f04682bf7c515abd67e159064d75771aaf885d2ae96c4366b229ef8fbb7' 'src/kuma.nnue' | sha256sum --check \
    && mkdir -p /opt/cope \
    && cd src \
    && sed -i 's/(1ULL << (x))/(uint64_t{1} << (x))/' defs.h \
    && sed -i 's/unsigned long long\& i : occupied_bb/uint64_t\& i : occupied_bb/' position.cpp \
    && sed -i 's/score score;/::score score;/' thread.h \
    && sed -i 's/dirty_piece dirty_piece;/::dirty_piece dirty_piece;/' nnue.h \
    && g++ -std=c++20 -DNDEBUG -O3 -march=x86-64-v3 -pthread -static \
        -include cstring -include fcntl.h -include sys/stat.h *.cpp -o /opt/cope/engine \
    && install -Dm644 ../LICENSE /opt/cope/LICENSE \
    && install -Dm644 unlicence.txt /opt/cope/INCBIN-LICENSE \
    && strip /opt/cope/engine

FROM scratch
WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./
ENTRYPOINT ["./engine"]
