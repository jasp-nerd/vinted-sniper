# Build stage: resolve and install dependencies into a self-contained virtualenv.
# Both base images are pinned by digest so a rebuild of the same commit gets the same bytes;
# Dependabot moves the digests forward weekly.
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim@sha256:e586c1c65b7071aec78f02c5587c76c5011159cd76b3d6f1255f40e48e850abc AS build

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
    uv sync --locked --no-install-project --no-dev

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --no-dev


# Runtime stage: the virtualenv and the code, nothing else.
FROM python:3.15-slim-trixie@sha256:94d3426f1def7db4a106a232ac121bfd975393e5dfd40a619b955fae45fe7913

# Static so a local build carries them too; the publish workflow adds version and revision.
LABEL org.opencontainers.image.title="vinted-bot" \
      org.opencontainers.image.description="Watch Vinted searches and get a message when a new listing matches." \
      org.opencontainers.image.source="https://github.com/jasp-nerd/vinted-bot" \
      org.opencontainers.image.url="https://github.com/jasp-nerd/vinted-bot" \
      org.opencontainers.image.documentation="https://github.com/jasp-nerd/vinted-bot/blob/main/docs/self-hosting.md" \
      org.opencontainers.image.licenses="MIT"

# curl is here for the health check and for the connection test in the troubleshooting
# guide, which is the first thing anyone reaches for when listings stop arriving.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 vinted \
    && useradd --system --create-home --uid 10001 --gid 10001 vinted

WORKDIR /app
COPY --from=build --chown=vinted:vinted /app/.venv /app/.venv
COPY --from=build --chown=vinted:vinted /app/src /app/src

# The privilege-dropping wrapper from docker/entrypoint.sh. It sits ahead of the real
# command on PATH, so the health check and `docker compose exec ... vinted-bot status`
# also run as the app's user rather than as root.
COPY --chmod=755 docker/entrypoint.sh /app/bin/vinted-bot

# The web host is 0.0.0.0 *inside the container* so the published port can reach it;
# docker-compose.yml maps it to the host's localhost only.
ENV PATH="/app/bin:/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    VINTED_BOT_DB_PATH=/data/app.db \
    VINTED_BOT_LOG_FORMAT=json \
    VINTED_BOT_WEB_HOST=0.0.0.0

RUN mkdir -p /data && chown vinted:vinted /data
VOLUME ["/data"]

# No USER line on purpose: the container starts as root only long enough for the wrapper
# to hand /data to PUID:PGID (default 10001, the `vinted` user), then the app runs as that
# user. Anyone who wants no root at all can set `user: "10001:10001"` instead.

EXPOSE 8000

# Checks that the app came round the loop recently, not merely that a process exists —
# a poller can be alive and stuck, which is the failure worth catching.
HEALTHCHECK --interval=60s --timeout=10s --start-period=45s --retries=3 \
    CMD ["vinted-bot", "heartbeat"]

ENTRYPOINT ["vinted-bot"]
CMD ["run"]
