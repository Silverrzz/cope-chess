FROM node:24-alpine AS frontend-build
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

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
COPY --from=docker-tools /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-tools /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
COPY --from=docker-tools /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose

FROM common-system AS client-runtime
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

FROM common-system AS runtime
ENV COPE_DATABASE_URL=postgresql://cope@db:5432/cope
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install --no-install-recommends -y postgresql-client
ARG COPE_BUILD_VERSION=0.1.0
ENV COPE_BUILD_VERSION=${COPE_BUILD_VERSION}
COPY pyproject.toml MANIFEST.in ./
COPY cope/ ./cope/
COPY data/engines/ ./data/engines/
COPY --from=frontend-build /src/cope/web/frontend_dist/ ./cope/web/frontend_dist/
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    printf '%s\n' "${COPE_BUILD_VERSION}" > cope/BUILD_VERSION \
    && python -m pip install ".[database,web,runner,worker]" \
    && mkdir -p /backups \
    && chown -R cope:cope /backups /app
USER cope
EXPOSE 8701 8702 8703
ENTRYPOINT ["cope"]
CMD ["web", "--host", "0.0.0.0", "--port", "8701"]
