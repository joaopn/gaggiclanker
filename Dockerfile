# gaggiclanker — one image, one process: uvicorn serving the API and the SPA.
#
# Three stages. The front-end build is separate so a Python-only change does not
# reinstall npm, and the runtime stage carries neither node nor a compiler.

# --------------------------------------------------------------------------
# Stage 1: the React bundle.
#
# node:22-alpine, matching the Node major the front end is developed against
# (web/README.md pins the local install to the same line).
#
# The manifest and the lockfile are copied first so `npm ci` is cached
# independently of the source: editing a component reuses the dependency layer,
# which is 90% of the build time. `npm ci` (not `install`) because the lockfile
# is committed and the image must not quietly resolve a different tree.
# --------------------------------------------------------------------------
FROM node:22-alpine AS web-build

WORKDIR /build

COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY web/ ./

# Type errors do not fail `vite build` (esbuild strips types without checking
# them), so the check is explicit: an image that compiles but does not type-check
# is a broken build that ships.
RUN npx tsc --noEmit && npm run build


# --------------------------------------------------------------------------
# Stage 2: the Python environment, built with uv into a self-contained .venv.
#
# Dependencies are installed from the lockfile before the source is copied, so
# editing a .py file reuses the cached dependency layer.
# --------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS python-build

# Pinned: uv resolves and installs the lockfile, so its version is part of what
# makes the build reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY pyproject.toml uv.lock README.md ./
COPY gaggiclanker/ ./gaggiclanker/

# --reinstall-package gaggiclanker is load-bearing, not belt-and-braces. The uv
# cache mount survives between builds, and the project's own wheel is cached
# under its name and version — which does not change between commits. Without
# this flag a rebuild happily installs the PREVIOUS build's code while the
# COPY layer above shows as changed, so the image silently ships something
# other than the source it was built from. Dependencies still come from cache.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --no-dev --reinstall-package gaggiclanker


# --------------------------------------------------------------------------
# Stage 3: the runtime. No uv, no node, no compiler — just Python and the venv.
# --------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

# uid/gid 1000 to match the usual first desktop user, so files the container
# writes into the bind-mounted data directory are owned by whoever runs it.
# Override at build time when the host user differs: --build-arg APP_UID=1001.
ARG APP_UID=1000
ARG APP_GID=1000

RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /usr/sbin/nologin app

# setpriv (util-linux) is how the entrypoint drops from root to the app user.
# Asserted at build time rather than discovered at first boot.
RUN command -v setpriv > /dev/null

WORKDIR /app

# The package itself is installed into the venv (uv sync --no-editable), so
# only the venv and the front-end bundle are copied here.
COPY --from=python-build --chown=app:app /app/.venv /app/.venv
COPY --from=web-build --chown=app:app /build/dist/ ./web/dist/

# The venv's bin first, so `python` and `gaggiclanker` are the installed ones.
# WEB_DIST is explicit because the installed package sits in site-packages and
# cannot find the bundle by walking up from its own file, as it does in a
# source checkout.
# HOME is set explicitly because setpriv does not rewrite it: without this the
# app would run with HOME=/root, which it cannot write. For the LLM layer it matters
# concretely — `claude -p` keeps its configuration and credentials under HOME.
ENV PATH="/app/.venv/bin:${PATH}" \
    HOME=/home/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/app/data \
    WEB_DIST=/app/web/dist \
    HOST=0.0.0.0 \
    PORT=8042 \
    LOG_LEVEL=info

# Created and owned here so a fresh *named volume* inherits the right ownership
# (Docker copies the image's ownership onto an empty volume). A *bind mount*
# gets none of that — the host directory shadows this one — which is what the
# entrypoint is for.
RUN mkdir -p /app/data/backups /home/app \
    && chown -R app:app /app/data /home/app

COPY --chmod=0755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Deliberately NOT `USER app`: the entrypoint starts as root purely to make
# DATA_DIR writable, then execs the app as uid ${APP_UID} via setpriv. No
# application code ever runs as root.
EXPOSE 8042

VOLUME ["/app/data"]

# Python rather than curl, so the image needs no extra package. It reads PORT
# from the environment so overriding the port does not break the check.
# start-period covers the first-boot migrations; three retries at 30 s means a
# wedged process is reported unhealthy inside two minutes.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8042'), timeout=4).status == 200 else 1)"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# The console script reads HOST/PORT/LOG_LEVEL from the same environment the
# app does, so `docker run -e PORT=9000` needs no change to this line.
CMD ["gaggiclanker"]
