"""FastAPI routers.

Each submodule exposes a `router: APIRouter` that the application's
composition root (main.py) mounts via `app.include_router(...)`.

Routers kept small and topically focused — when adding an endpoint,
prefer slotting it into the most-relevant existing router over creating
a new one; create new routers only when they have a clearly distinct
subject (a new resource, a new backend service surface, etc.).
"""
