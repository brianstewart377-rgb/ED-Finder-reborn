FROM ghcr.io/brianstewart377-rgb/ed-finder/mirror/python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

WORKDIR /app

# Runtime libs: libpq+gcc for asyncpg build fallbacks; freetype+
# zlib+jpeg+dejavu fonts for Pillow's OG share-card renderer.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libpq-dev gcc \
    libjpeg62-turbo zlib1g libfreetype6 fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

# Build context = repo root so the image can vendor shared_contracts. Local and
# Review Lab images use the same frozen V3 runtime dependency graph as release.
RUN python -m pip install --no-cache-dir uv==0.11.33
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN python -c "import platform, sys; assert platform.python_implementation() == 'CPython'; assert sys.version_info[:2] == (3, 14)" && \
    test "$(python -m uv --version | cut -d' ' -f1-2)" = "uv 0.11.33" && \
    python -m uv sync --frozen --no-dev --no-group test --no-install-project

# Copy the API source tree as-is. The committed `edfinder_api` package
# namespace gives us a stable runtime entrypoint while the remaining
# legacy flat imports are migrated incrementally.
COPY apps/api/src/ ./
COPY shared_contracts/ ./shared_contracts/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

EXPOSE 8000
# workers=1 — backend keeps per-process state (SSE clients, active jobs,
# rate-limit buckets, metrics). Scale horizontally via more containers
# behind nginx if needed, not via uvicorn workers.
CMD ["uvicorn", "edfinder_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
