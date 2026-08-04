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
    && sed -i "/import sys/a os.environ.setdefault('NUMBA_CACHE_LOCATOR_CLASSES', 'InTreeCacheLocatorFsAgnostic')" numba_engine_PRE_NNUE_clean.py \
    && pyinstaller \
        --onedir \
        --noconfirm \
        --clean \
        --name numbengine \
        --console \
        --collect-all numba \
        --collect-all llvmlite \
        numba_engine_PRE_NNUE_clean.py \
    && cp -a numba_engine_PRE_NNUE_clean.py dist/numbengine/

RUN cd dist/numbengine \
    && ./numbengine --warmup

RUN printf '%s\n' \
        '#!/bin/sh' \
        'engine_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)' \
        'runtime_dir="${TMPDIR:-/tmp}/numbengine-${engine_dir##*/}"' \
        'mkdir -p "$runtime_dir"' \
        'cp -R "$engine_dir/__pycache__" "$runtime_dir/"' \
        'cp "$engine_dir/numba_engine_PRE_NNUE_clean.py" "$runtime_dir/"' \
        'cd "$runtime_dir" || exit 1' \
        'exec "$engine_dir/numbengine" "$@"' \
        > dist/numbengine/engine \
    && find dist/numbengine -type d -exec chmod 755 {} + \
    && find dist/numbengine -type f -exec chmod 644 {} + \
    && chmod 755 dist/numbengine/engine dist/numbengine/numbengine \
    && mkdir /opt/cope \
    && cp -aL dist/numbengine/. /opt/cope/

FROM debian:bookworm-slim

WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./

ENTRYPOINT ["./engine"]
