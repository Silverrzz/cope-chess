FROM node:24-bookworm-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54

ARG COPE_REVISION
LABEL org.opencontainers.image.source="https://github.com/Silverrzz/cope-chess" \
      org.opencontainers.image.revision="${COPE_REVISION}"
RUN apt-get update \
    && apt-get install --no-install-recommends -y python3 openssl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app/.cope-worker /state \
    && chown node:node /app/.cope-worker /state
WORKDIR /app/bootstrap
RUN mkdir -p src/styles quorum-web
COPY --chmod=644 frontend/quorum-runtime/package.json frontend/quorum-runtime/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund && npm cache clean --force
COPY --chmod=644 frontend/quorum-node.mjs frontend/quorum-web.mjs frontend/quorum-audit.mjs ./
COPY --chmod=644 frontend/quorum-supervisor.py frontend/quorum_updates.py ./
COPY --chmod=644 frontend/quorum-web/index.html frontend/quorum-web/app.js frontend/quorum-web/style.css ./quorum-web/
COPY --chmod=644 frontend/src/quorum.ts ./src/quorum.ts
COPY --chmod=644 frontend/src/styles/tokens.css ./src/styles/tokens.css
RUN node --input-type=module -e '\
    import {writeFileSync} from "node:fs"; \
    const commit = process.argv[1]; \
    if (!/^[a-f0-9]{40}$/.test(commit)) throw Error("Set COPE_REVISION to the reviewed checkout commit"); \
    writeFileSync("verifier.json", JSON.stringify({commit, node: process.execPath, bootstrap: process.cwd(), origin: "https://cope-chess.live"}))' "${COPE_REVISION}"
ENV NODE_ENV=production \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    COPE_VERIFIER_BIND=0.0.0.0 \
    COPE_VERIFIER_ARCHIVE=/state
USER node
EXPOSE 8788
VOLUME ["/state", "/app/.cope-worker"]
ENTRYPOINT ["python3", "/app/bootstrap/quorum-supervisor.py", "/app/bootstrap/verifier.json", "/app/.cope-worker/installer/quorum-verifier/v1"]
