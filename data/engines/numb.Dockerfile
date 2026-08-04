FROM python:3.13.11-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update \
    && apt-get install --no-install-recommends -y binutils libgomp1 libtbb12 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install \
        numpy==2.4.6 \
        llvmlite==0.48.0 \
        numba==0.66.0 \
        altgraph==0.17.5 \
        packaging==26.2 \
        pyinstaller-hooks-contrib==2026.6 \
        pyinstaller==6.21.0 \
        setuptools==83.0.0

COPY . .

RUN rm -rf \
        /usr/local/lib/python3.13/site-packages/numba/tests \
        /usr/local/lib/python3.13/site-packages/llvmlite/tests \
    && strip --strip-unneeded /usr/local/lib/python3.13/site-packages/llvmlite/binding/libllvmlite.so \
    && sed -i "/import sys/a os.environ.setdefault('NUMBA_CPU_NAME', 'haswell')" numba_engine_PRE_NNUE_clean.py \
    && pyinstaller \
        --onedir \
        --noconfirm \
        --clean \
        --name numbengine \
        --console \
        --collect-all numba \
        --collect-all llvmlite \
        numba_engine_PRE_NNUE_clean.py \
    && dist/numbengine/numbengine --warmup \
    && mv dist/numbengine/numbengine dist/numbengine/engine \
    && find dist/numbengine -type d -exec chmod 755 {} + \
    && find dist/numbengine -type f -exec chmod 644 {} + \
    && chmod 755 dist/numbengine/engine \
    && mkdir /opt/cope \
    && cp -aL dist/numbengine/. /opt/cope/

FROM debian:bookworm-slim

WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./

ENTRYPOINT ["./engine"]
