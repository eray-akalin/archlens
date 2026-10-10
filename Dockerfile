# syntax=docker/dockerfile:1
# ArchLens image (docs/AZURE.md §4), linux/amd64. One image for the API (`archlens serve`, the
# default command) and the worker (`archlens worker`, M4.3). Base images are pinned by digest;
# scanners come from config/tools.yaml with checksum verification (scripts/fetch_scanners.py).
# Semgrep registry rules are not included — their license forbids redistribution.

ARG PYTHON_IMAGE=python:3.12-slim-trixie@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.15@sha256:e590846f4776907b254ac0f44b5b380347af5d90d668138ca7938d1b0c2f98d3

FROM ${UV_IMAGE} AS uv

# --- build: the locked environment and the pinned scanners --------------------------------------
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_PREFERENCE=only-system \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable
COPY config ./config
COPY scripts/fetch_scanners.py ./scripts/
RUN /app/.venv/bin/python scripts/fetch_scanners.py --dest /opt/scanners

# --- runtime ------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.source="https://github.com/eray-akalin/archlens" \
      org.opencontainers.image.description="ArchLens: evidence-backed architecture assessment" \
      org.opencontainers.image.licenses="MIT"
# git: the hardened clone of assessed repositories (nothing from them is ever executed). Package
# versions float within the digest-pinned Debian release's security updates, hence DL3008.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home --home-dir /home/archlens archlens \
    && mkdir -p /data \
    && chown archlens:archlens /data
COPY --from=build /app/.venv /app/.venv
COPY --from=build /opt/scanners /opt/scanners
WORKDIR /app
COPY config ./config
COPY rubrics ./rubrics
COPY prompts ./prompts
ENV PATH="/app/.venv/bin:/opt/scanners/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ARCHLENS_DATA_DIR=/data \
    ARCHLENS_SEMGREP_REGISTRY_RULES=false \
    ARCHLENS_TOOLS_DIR=/home/archlens/.cache/archlens/tools
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
CMD ["archlens", "serve", "--host", "0.0.0.0", "--port", "8000"]
