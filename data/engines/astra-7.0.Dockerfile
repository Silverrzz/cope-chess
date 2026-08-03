FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build

ADD --checksum=sha256:7fcb7cc774315714d13ccf1fb337f3affd53710b98bc1575c0542a1dc05b9753 https://github.com/h1me01/Astra-Networks/releases/download/weights/weights-v3-perm.nnue /build/weights-v3-perm.nnue
COPY . .

RUN sed -i \
        -e 's/-march=native/-march=x86-64-v3/' \
        -e 's/ -flto / /' \
        makefile \
    && make -j"$(nproc)" \
        CXX=g++ \
        STATIC="-static" \
        EVALFILE=weights-v3-perm.nnue \
        EXE=astra \
    && install -Dm755 astra /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
