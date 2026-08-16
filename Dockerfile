# Build stage: resolve and install dependencies into a self-contained virtualenv.
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies change far less often than the code, so they get their own layer and their
# own cache mount. Editing a source file then costs a rebuild of the last layer only.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=README.md,target=README.md \
    uv sync --locked --no-install-project --no-dev --extra web

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --no-dev --extra web


# Runtime stage: the virtualenv and the code, nothing else.
FROM python:3.13-slim-trixie

# curl is here for the health check and for the connection test in the troubleshooting
# guide, which is the first thing anyone reaches for when listings stop arriving.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home --uid 10001 sniper

WORKDIR /app
COPY --from=build --chown=sniper:sniper /app/.venv /app/.venv
COPY --from=build --chown=sniper:sniper /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    VINTED_SNIPER_DB_PATH=/data/app.db \
    VINTED_SNIPER_LOG_FORMAT=json

RUN mkdir -p /data && chown sniper:sniper /data
VOLUME ["/data"]
USER sniper

EXPOSE 8000

# Checks that the app came round the loop recently, not merely that a process exists —
# a poller can be alive and stuck, which is the failure worth catching.
HEALTHCHECK --interval=60s --timeout=10s --start-period=45s --retries=3 \
    CMD ["vinted-sniper", "heartbeat"]

ENTRYPOINT ["vinted-sniper"]
CMD ["run"]
