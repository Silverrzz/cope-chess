FROM node:24-alpine AS frontend-build
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
RUN npm prune --omit=dev --ignore-scripts

FROM node:24-bookworm-slim AS quorum-node

FROM quorum-node AS verifier-runtime
WORKDIR /opt/quorum
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts \
    && mkdir -p /var/lib/cope-quorum-verifier \
    && chown node:node /var/lib/cope-quorum-verifier
COPY frontend/quorum-node.mjs frontend/quorum-audit.mjs ./
COPY frontend/src/quorum.ts ./src/quorum.ts
USER node
VOLUME /var/lib/cope-quorum-verifier
EXPOSE 8788
ENTRYPOINT ["node"]
CMD ["quorum-node.mjs"]

FROM docker:28-cli AS docker-tools

FROM python:3.13-slim-bookworm AS common-system
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COPE_ENGINE_DOCKERFILES_DIR=/app/data/engines
WORKDIR /app
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        git \
        git-lfs \
    && groupadd --system --gid 10001 cope \
    && useradd --system --uid 10001 --gid cope --home-dir /app cope

FROM common-system AS docker-system
COPY --from=docker-tools /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-tools /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
COPY --from=docker-tools /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose

FROM docker-system AS client-runtime
ARG COPE_BUILD_VERSION=0.1.0
ENV COPE_BUILD_VERSION=${COPE_BUILD_VERSION}
COPY pyproject.toml MANIFEST.in ./
COPY cope/ ./cope/
COPY data/engines/ ./data/engines/
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    printf '%s\n' "${COPE_BUILD_VERSION}" > cope/BUILD_VERSION \
    && python -m pip install ".[worker]" \
    && chown -R cope:cope /app
USER cope
ENTRYPOINT ["cope"]

FROM common-system AS worker-runtime
ARG COPE_BUILD_VERSION=0.1.0
ENV COPE_BUILD_VERSION=${COPE_BUILD_VERSION} \
    COPE_DISABLE_CLIENT_UPDATES=1 \
    COPE_WORKER_ENGINE_DIR=/var/lib/cope-worker/engines
COPY pyproject.toml MANIFEST.in ./
COPY cope/ ./cope/
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    printf '%s\n' "${COPE_BUILD_VERSION}" > cope/BUILD_VERSION \
    && python -m pip install ".[worker]" \
    && mkdir -p /var/lib/cope-worker/engines \
    && chown -R cope:cope /var/lib/cope-worker /app
USER cope
VOLUME ["/var/lib/cope-worker"]
ENTRYPOINT ["cope"]
CMD ["worker", "--state-file", "/var/lib/cope-worker/worker.json"]

FROM docker-system AS runtime
ENV COPE_DATABASE_URL=postgresql://cope@db:5432/cope
ADD --chmod=0644 --checksum=sha256:0144068502a1eddd2a0280ede10ef607d1ec592ce819940991203941564e8e76 https://www.postgresql.org/media/keys/ACCC4CF8.asc /usr/share/keyrings/cope-postgresql.asc
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    printf '%s\n' 'deb [signed-by=/usr/share/keyrings/cope-postgresql.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main' > /etc/apt/sources.list.d/cope-postgresql.list \
    && apt-get update \
    && apt-get install --no-install-recommends -y libstdc++6 postgresql-client-17
ARG COPE_BUILD_VERSION=0.1.0
ENV COPE_BUILD_VERSION=${COPE_BUILD_VERSION}
COPY pyproject.toml MANIFEST.in ./
COPY cope/ ./cope/
COPY data/engines/ ./data/engines/
COPY --from=frontend-build /src/cope/web/frontend_dist/ ./cope/web/frontend_dist/
COPY --from=quorum-node /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend-build /src/frontend/node_modules/ /opt/quorum/node_modules/
COPY frontend/quorum-verifier.mjs /opt/quorum/quorum-verifier.mjs
ENV COPE_QUORUM_VERIFIER=/opt/quorum/quorum-verifier.mjs
ADD --chmod=0644 --checksum=sha256:1e9e1e3ad9917164bc96c751e32fae724400b92063f5a2cfd22ca64674bf4f96 https://snark-artifacts.pse.dev/semaphore/4.13.0/semaphore-10.wasm /app/cope/web/frontend_dist/assets/quorum/semaphore-10.wasm
ADD --chmod=0644 --checksum=sha256:f644af6753be9d48454d8f9bb74bf2e9a416dd7d1a704c3cf1d50e535969a19a https://snark-artifacts.pse.dev/semaphore/4.13.0/semaphore-10.zkey /app/cope/web/frontend_dist/assets/quorum/semaphore-10.zkey
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    printf '%s\n' "${COPE_BUILD_VERSION}" > cope/BUILD_VERSION \
    && python -m pip install ".[database,web,runner,worker]" \
    && mkdir -p /backups /var/lib/cope/engine-artifacts /var/lib/cope/clone-transfer /var/lib/cope/artifacts \
    && chown -R cope:cope /backups /var/lib/cope /app
USER cope
EXPOSE 8701 8702 8703
ENTRYPOINT ["cope"]
CMD ["web", "--host", "0.0.0.0", "--port", "8701"]
