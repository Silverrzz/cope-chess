FROM silkeh/clang:18-bookworm AS builder

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates cmake git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .

RUN git init . \
    && git remote add origin https://github.com/official-clockwork/Clockwork.git \
    && git fetch --depth 1 origin 7f4099f6453422f1835c9c56dac85035996b9e00 \
    && git update-ref HEAD FETCH_HEAD \
    && git init vendor/lps \
    && git -C vendor/lps remote add origin https://github.com/87flowers/lps.git \
    && git -C vendor/lps fetch --depth 1 origin 392ee574034b89739928de528e41ed4cab9482e8 \
    && git -C vendor/lps -c advice.detachedHead=false checkout FETCH_HEAD \
    && cmake -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=clang++ \
        -DCLOCKWORK_MARCH_TARGET=x86-64-v3 \
    && cmake --build build --target clockwork --parallel "$(nproc)" \
    && install -Dm755 build/clockwork /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
