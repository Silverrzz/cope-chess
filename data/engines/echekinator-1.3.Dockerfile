FROM debian:trixie-slim AS builder

RUN apt-get update \
    && apt-get install --no-install-recommends -y gcc libc6-dev ocaml-nox ocaml-dune ocaml-findlib libmtime-ocaml-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .

RUN sed -i 's/(libraries libs))/(libraries libs) (link_flags -ccopt -static))/' bin/dune \
    && dune build --profile release bin/echekinator.exe \
    && install -Dm755 _build/default/bin/echekinator.exe /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
