FROM node:24-alpine AS frontend-build
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM docker:28-cli AS docker-tools

FROM python:3.13-slim-bookworm AS runtime
ARG COPE_BUILD_VERSION=0.1.0
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COPE_DATABASE_URL=postgresql://cope@db:5432/cope \
    COPE_BUILD_VERSION=${COPE_BUILD_VERSION}
WORKDIR /app
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        git \
        git-lfs \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 cope \
    && useradd --system --uid 10001 --gid cope --home-dir /app cope
COPY --from=docker-tools /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-tools /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
COPY pyproject.toml MANIFEST.in ./
COPY cope/ ./cope/
COPY --from=frontend-build /src/cope/web/frontend_dist/ ./cope/web/frontend_dist/
RUN printf '%s\n' "${COPE_BUILD_VERSION}" > cope/BUILD_VERSION \
    && python -m pip install --no-cache-dir ".[database,web,runner,worker]" \
    && mkdir -p /backups \
    && chown -R cope:cope /backups /app
USER cope
EXPOSE 8701 8702 8703
ENTRYPOINT ["cope"]
CMD ["web", "--host", "0.0.0.0", "--port", "8701"]
