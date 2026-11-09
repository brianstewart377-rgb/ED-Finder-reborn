# syntax=docker/dockerfile:1
# V3 EDDN Docked station-sighting worker. NOT wired into any compose file,
# release workflow or production deploy authority; see README.md.
FROM ghcr.io/brianstewart377-rgb/ed-finder/mirror/python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS build

WORKDIR /app

# uv is itself pinned; dependencies resolve only from the committed lock.
# psycopg[binary] and pyzmq ship manylinux wheels, so no compiler is needed.
RUN python -m pip install --no-cache-dir uv==0.11.33
COPY apps/eddn_v3/pyproject.toml apps/eddn_v3/uv.lock ./
RUN python -c "import platform, sys; assert platform.python_implementation() == 'CPython'; assert sys.version_info[:2] == (3, 14)" && \
    test "$(python -m uv --version | cut -d' ' -f1-2)" = "uv 0.11.33" && \
    python -m uv sync --frozen --no-dev --no-install-project

FROM ghcr.io/brianstewart377-rgb/ed-finder/mirror/python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS runtime

ARG BUILD_SHA
WORKDIR /app

RUN case "$BUILD_SHA" in \
      *[!0-9a-f]*|'') exit 64 ;; \
    esac && [ "${#BUILD_SHA}" -eq 40 ]

RUN groupadd --system --gid 10017 observer && \
    useradd --system --uid 10017 --gid observer --no-create-home --shell /usr/sbin/nologin observer

COPY --from=build /app/.venv /app/.venv
RUN /app/.venv/bin/python -c "import platform, sys; assert platform.python_implementation() == 'CPython'; assert sys.version_info[:2] == (3, 14)"
COPY apps/eddn_v3/src/edfinder_observe/ ./edfinder_observe/

ENV PATH="/app/.venv/bin:$PATH" \
    BUILD_SHA="$BUILD_SHA" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
LABEL org.opencontainers.image.revision="$BUILD_SHA"

USER observer:observer

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-m", "edfinder_observe.healthcheck"]

CMD ["python", "-m", "edfinder_observe"]
