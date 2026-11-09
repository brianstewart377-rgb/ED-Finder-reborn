"""
Share / OpenGraph router for ED Finder.

Exposes two endpoints used to make shared system links unfurl into
rich-media preview cards in Discord, Reddit, Twitter, Slack, etc.

  GET /s/{id64}              ── HTML page with OG meta tags. Bots stop
                                here and read the meta. Humans are 302'd
                                to the canonical Inspect route
                                `/inspect?system={id64}` without any
                                database read.
  GET /api/share/og/{id64}   ── PNG (1200x630) preview card rendered
                                with Pillow. Lazy: cached in Redis for
                                7 days under `og:v3:{generation_id}:{id64}`.

Both lookups read the published V3 canonical generation through the same
generation-pinned resolver as `/api/v1/systems/{id}` (`routers/v3.py`). The
legacy V2 `systems`/`ratings` relations do not exist in V3 production, so
the card carries only canonical facts (name, coordinates, catalogued body
count) — there is no V3 single "score"/rationale to show. An unknown system
is a clean 404; no published generation or a failed read is a 503.

The router is installed by `main.py` via `app.include_router(...)`.
Adding a router (rather than inlining in main.py) keeps the OG-image
code, which is large and dependency-heavy (Pillow), out of the hot
path and the 2k-line main module.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import html
import io
import logging
import os
import re
import uuid
from functools import partial
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from PIL import Image, ImageDraw, ImageFont

from edfinder_api.config import limiter
from edfinder_api.cpu_work import CpuWorkBusy, run_cpu_bound
from edfinder_api.deps import cache_get, cache_set, get_readonly_pool
from edfinder_api.routers.v3 import current_canonical_generation

log = logging.getLogger(__name__)

router = APIRouter(tags=["share"])

# Bot user-agents we serve OG-tagged HTML to instead of redirecting.
# Anyone NOT in this set gets bounced to the SPA (faster on real users).
BOT_UAS = (
    "discordbot", "twitterbot", "slackbot", "facebookexternalhit",
    "linkedinbot", "redditbot", "telegrambot", "whatsapp", "embedly",
    "googlebot", "bingbot", "applebot", "skypeuripreview", "pinterestbot",
    "iframely", "developers.google.com/+/web/snippet",
)

# Pillow doesn't ship a default font on most slim base images. We try a
# few system paths; if none exist, fall back to PIL's bitmap default
# (ugly but never crashes).
def _font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                pass
    return ImageFont.load_default()


def _wrap(text: str, max_chars: int) -> list[str]:
    """Naive word-wrap that's fine for one-line rationales (≤ 160 chars)."""
    if not text:
        return []
    words = text.split()
    lines, cur = [], ""
    for w in words:
        nxt = (cur + " " + w).strip()
        if len(nxt) <= max_chars:
            cur = nxt
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


INT64_MAX = 9_223_372_036_854_775_807
LOOKUP_TIMEOUT_SECONDS = 5.0
_DB_ERRORS = (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError)
ShareId64 = Path(ge=0, description="Unsigned decimal system id64")


class ShareSystem:
    """Canonical facts the preview may show, from one pinned generation."""

    __slots__ = ("generation_id", "id64", "name", "x", "y", "z", "body_count")

    def __init__(self, generation_id: str, row: asyncpg.Record) -> None:
        self.generation_id = generation_id
        self.id64 = int(row["id64"])
        self.name = str(row["name"])
        self.x = float(row["x_ly"])
        self.y = float(row["y_ly"])
        self.z = float(row["z_ly"])
        self.body_count = int(row["loaded_body_count"])


async def _resolve_generation(pool: asyncpg.Pool) -> tuple[str, str]:
    """(relation_schema, generation_id) of the published canonical generation.

    Bounded by the same lookup timeout as the system read. Raises 503 when
    nothing is published or the read fails or stalls: the preview never falls
    back to legacy V2 relations or invents a system.
    """
    try:
        schema, generation = await asyncio.wait_for(
            current_canonical_generation(pool), timeout=LOOKUP_TIMEOUT_SECONDS,
        )
    except HTTPException:
        raise
    except _DB_ERRORS as exc:
        log.warning("share canonical generation lookup failed: %s", type(exc).__name__)
        raise HTTPException(503, "System preview is unavailable") from exc
    return schema, generation.generation_id


_GENERATION_SCHEMA = re.compile(r"v3_gen_[a-z][a-z0-9_]{0,30}")


async def _resolve_pinned_generation(pool: asyncpg.Pool,
                                     generation_id: uuid.UUID) -> tuple[str, str]:
    """(relation_schema, generation_id) of a specific, once-published generation.

    Only a generation that was published and whose relations still exist can
    be pinned; anything else is a 404 (the link outlived its generation).
    """
    try:
        row = await asyncio.wait_for(
            pool.fetchrow(
                """SELECT relation_schema
                     FROM v3_meta.canonical_generation
                    WHERE generation_id=$1 AND published_at IS NOT NULL
                      AND lifecycle_state IN ('PUBLISHED','RETIRED')
                      AND to_regnamespace(relation_schema) IS NOT NULL""",
                generation_id,
                timeout=LOOKUP_TIMEOUT_SECONDS,
            ),
            timeout=LOOKUP_TIMEOUT_SECONDS,
        )
    except _DB_ERRORS as exc:
        log.warning("share pinned generation lookup failed: %s", type(exc).__name__)
        raise HTTPException(503, "System preview is unavailable") from exc
    if row is None:
        raise HTTPException(404, "Preview generation is not available")
    schema = str(row["relation_schema"])
    if not _GENERATION_SCHEMA.fullmatch(schema):
        raise HTTPException(500, "Unsafe canonical generation relation schema")
    return schema, str(generation_id)


async def _lookup_system(pool: asyncpg.Pool, schema: str, generation_id: str,
                         id64: int) -> Optional[ShareSystem]:
    """One bounded read of the canonical system row; None when absent."""
    if id64 > INT64_MAX:
        # Canonical id64 is a signed bigint: a larger unsigned id cannot exist.
        return None
    try:
        # Bound the whole pool operation: Pool.fetchrow does not apply its
        # timeout to connection acquisition, so a saturated pool must still
        # become the retryable 503 within the lookup bound.
        row = await asyncio.wait_for(
            pool.fetchrow(
                f"""SELECT id64,name,x_ly,y_ly,z_ly,loaded_body_count
                      FROM {schema}.systems WHERE id64=$1""",
                id64,
                timeout=LOOKUP_TIMEOUT_SECONDS,
            ),
            timeout=LOOKUP_TIMEOUT_SECONDS,
        )
    except _DB_ERRORS as exc:
        log.warning("share system lookup failed for %s: %s", id64, type(exc).__name__)
        raise HTTPException(503, "System preview is unavailable") from exc
    return ShareSystem(generation_id, row) if row is not None else None


def _summary(system: ShareSystem) -> str:
    bodies = "1 catalogued body" if system.body_count == 1 else f"{system.body_count:,} catalogued bodies"
    return f"{bodies}. Open in ED-Finder to inspect its stars and worlds."


_PUBLIC_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::[0-9]{1,5})?")
_DEFAULT_PUBLIC_HOST = "ed-finder.app"


def _public_base(request: Request) -> str:
    """Absolute public origin for OG tags (bots fetch og:image themselves).

    Built from the X-Forwarded-* headers nginx sets rather than
    request.base_url (the proxy-internal http://127.0.0.1). Both header values
    are untrusted input: the proto must be exactly http/https and the host a
    plain hostname[:port]; anything else falls back to the public default.
    """
    host = request.headers.get("x-forwarded-host") or request.url.hostname or ""
    proto = request.headers.get("x-forwarded-proto") or "https"
    if not _PUBLIC_HOST.fullmatch(host):
        host = _DEFAULT_PUBLIC_HOST
    # Plain http is honoured only for local development hosts. A public,
    # dotted host is always advertised as https: the public site terminates
    # TLS in front of the edge, and an intermediate proxy that rewrites the
    # scheme to its own plain-http hop must not leak http:// into share cards.
    if proto != "http" or not _is_local_host(host):
        proto = "https"
    return f"{proto}://{host}"


def _is_local_host(host: str) -> bool:
    name = host.rsplit(":", 1)[0].lower() if ":" in host else host.lower()
    return name in ("localhost", "127.0.0.1") or name.endswith(".localhost") or "." not in name


def _plain_page(status_code: int, title: str, message: str, inspect_url: str) -> HTMLResponse:
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{_html_escape(title)}</title>
  <meta name="robots" content="noindex">
</head>
<body>
  <h1>{_html_escape(title)}</h1>
  <p>{_html_escape(message)}</p>
  <p><a href="{_html_escape(inspect_url)}">Open in ED Finder</a></p>
</body>
</html>"""
    return HTMLResponse(html, status_code=status_code)


# ─────────────────────────────────────────────────────────────────────────
# HTML stop-page for bots
# ─────────────────────────────────────────────────────────────────────────
@router.get("/s/{id64}")
@limiter.shared_limit("60/minute", scope="share_stop_page")
async def share_stop_page(id64: int = ShareId64, *, request: Request,
                          pool: asyncpg.Pool = Depends(get_readonly_pool)):
    """
    Bots → static HTML with OG/Twitter meta tags pointing at the PNG
    endpoint below. Real users → 302 to the canonical Inspect route
    (`/inspect?system={id64}`), which owns its own found/not-found states.
    """
    ua = (request.headers.get("user-agent") or "").lower()
    is_bot = any(b in ua for b in BOT_UAS)

    if not is_bot:
        # Real user → bounce to the SPA before any database work. The target
        # is same-origin and relative: no request header shapes the redirect.
        return RedirectResponse(url=f"/inspect?system={int(id64)}", status_code=302)

    base = _public_base(request)
    sys_url = f"{base}/inspect?system={id64}"

    try:
        schema, generation_id = await _resolve_generation(pool)
        system = await _lookup_system(pool, schema, generation_id, id64)
    except HTTPException as exc:
        if exc.status_code == 503:
            return _plain_page(503, "ED Finder", "System preview is temporarily unavailable.", sys_url)
        raise
    # The advertised card is pinned to the generation this page describes.
    img_url = f"{base}/api/share/og/{id64}?generation={generation_id}"
    if system is None:
        return _plain_page(404, "System not found — ED Finder",
                           f"No catalogue system exists for id64 {id64}.", sys_url)

    title = f"{system.name} — ED Finder"
    desc = (f"Coordinates {system.x:.1f} / {system.y:.1f} / {system.z:.1f} ly · "
            f"{_summary(system)}")

    # Bot → serve OG-tagged HTML.
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{_html_escape(title)}</title>
  <meta name="description" content="{_html_escape(desc)}">

  <!-- OpenGraph — Facebook, Discord, Slack, LinkedIn, etc. -->
  <meta property="og:type" content="website">
  <meta property="og:title" content="{_html_escape(title)}">
  <meta property="og:description" content="{_html_escape(desc)}">
  <meta property="og:image" content="{_html_escape(img_url)}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="og:url" content="{_html_escape(sys_url)}">
  <meta property="og:site_name" content="ED Finder">

  <!-- Twitter Card — Twitter / X -->
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{_html_escape(title)}">
  <meta name="twitter:description" content="{_html_escape(desc)}">
  <meta name="twitter:image" content="{_html_escape(img_url)}">

  <link rel="canonical" href="{_html_escape(sys_url)}">
</head>
<body>
  <h1>{_html_escape(title)}</h1>
  <p>{_html_escape(desc)}</p>
  <p><a href="{_html_escape(sys_url)}">Open in ED Finder</a></p>
</body>
</html>"""
    return HTMLResponse(html)


def _html_escape(value: str) -> str:
    """Escape text and attribute values for the HTML pages (stdlib escaper)."""
    return html.escape(value or "", quote=True)


OG_CACHE_TTL_SECONDS = 7 * 24 * 3600
# A rendered 1200x630 card is a few tens of KB; refuse anything absurd from
# the cache rather than streaming it to a client.
OG_CACHE_MAX_BYTES = 2 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _encode_cached_png(png: bytes) -> str:
    return base64.b64encode(png).decode("ascii")


def _decode_cached_png(value: object) -> Optional[bytes]:
    """Turn a cache_get() result back into PNG bytes, or None if unusable."""
    if not isinstance(value, str) or not value:
        return None
    if len(value) > OG_CACHE_MAX_BYTES * 4 // 3 + 4:
        return None
    try:
        png = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not png.startswith(PNG_SIGNATURE) or len(png) > OG_CACHE_MAX_BYTES:
        return None
    return png


# ─────────────────────────────────────────────────────────────────────────
# OG image renderer
# ─────────────────────────────────────────────────────────────────────────
@router.get("/api/share/og/{id64}")
@limiter.shared_limit("20/minute", scope="og_image")
async def og_image(id64: int = ShareId64, *, request: Request,
                   generation: Optional[uuid.UUID] = Query(default=None),
                   pool: asyncpg.Pool = Depends(get_readonly_pool)):
    """
    Render a 1200×630 PNG preview card for the given system.

    The stop page advertises the image pinned to the generation it described
    (`?generation=<id>`), so a publication between the two bot fetches cannot
    pair one generation's title with another's card; a pinned card is
    immutable and cacheable for 7 days. Without the pin the card follows the
    current publication and is cached publicly for only an hour. Redis keys
    always carry the generation.
    """
    redis = getattr(request.app.state, "redis", None)
    if generation is None:
        schema, generation_id = await _resolve_generation(pool)
        cache_control = "public, max-age=3600"
    else:
        schema, generation_id = await _resolve_pinned_generation(pool, generation)
        cache_control = "public, max-age=604800, immutable"
    cache_key = f"og:v3:{generation_id}:{id64}"

    # 1. Cache hit — stream straight from Redis. Goes through deps.cache_get
    #    so the shared cache I/O timeout applies: a slow or wedged Redis must
    #    not hold an OG-image request (Discord/Slack unfurl) hostage. The
    #    PNG is stored base64-encoded because the shared client runs with
    #    decode_responses=True and the helpers are JSON-shaped.
    cached_png = _decode_cached_png(await cache_get(cache_key, redis))
    if cached_png is not None:
        return Response(
            content=cached_png,
            media_type="image/png",
            headers={"Cache-Control": cache_control, "X-Cache": "HIT"},
        )

    # 2. Live fetch canonical system facts from the same pinned generation.
    system = await _lookup_system(pool, schema, generation_id, id64)
    if system is None:
        raise HTTPException(404, f"System {id64} not found")

    # 3. Render the image.
    try:
        png = await run_cpu_bound(partial(
            _render_card,
            title=system.name,
            summary=_summary(system),
            coords=(system.x, system.y, system.z),
            id64=id64,
        ))
    except CpuWorkBusy as exc:
        raise HTTPException(
            503, "Server busy, try again shortly", headers={"Retry-After": "2"},
        ) from exc

    # 4. Cache for 7 days (same bounded helper; failures are swallowed there).
    await cache_set(cache_key, _encode_cached_png(png), OG_CACHE_TTL_SECONDS, redis)

    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": cache_control, "X-Cache": "MISS"},
    )


def _render_card(
    title: str,
    summary: str,
    coords: tuple[float | None, float | None, float | None],
    id64: int,
) -> bytes:
    """
    Produce a 1200×630 PNG matching Twitter Card / OG dimensions.

    Layout (left-aligned, dark theme):
      ┌──────────────────────────────────────────────────────────────┐
      │  ED FINDER                                                   │
      │                                                              │
      │  System Name                                                 │
      │  Coords  -1234.56 / 12.34 / 5678.90 LY                       │
      │                                                              │
      │  ▸ 41 catalogued bodies. Open in ED-Finder to inspect …     │
      │                                                              │
      │  ED-Finder  ·  /s/{id64}                                     │
      └──────────────────────────────────────────────────────────────┘

    Only canonical V3 facts are drawn: there is no V3 single score,
    confidence or rationale, so none is shown.
    """
    W, H = 1200, 630
    BG, FG, DIM = (10, 8, 18), (255, 240, 220), (170, 160, 150)
    ORANGE = (255, 170, 60)

    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Soft warm radial glow on the right (nods to the BH visual on the site).
    # Pillow has no native gradient; we fake it with a few overlapping
    # alpha-blended ellipses, which is fast and good enough.
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for i, alpha in enumerate([24, 16, 8]):
        rr = 220 + i * 110
        od.ellipse([W - rr, H // 2 - rr, W + rr, H // 2 + rr],
                   fill=(255, 100, 30, alpha))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    # ── Header strip (brand top-left) ─────────────────────────────────
    f_brand = _font(28)
    draw.text((48, 36), "ED FINDER", font=f_brand, fill=ORANGE)

    # ── Title (system name) ───────────────────────────────────────────
    # Truncate long names so the layout never overflows.
    name = (title or f"System #{id64}").strip()
    if len(name) > 36:
        name = name[:33] + "…"
    f_title = _font(72)
    draw.text((48, 110), name, font=f_title, fill=FG)

    # ── Coords subtitle ────────────────────────────────────────────────
    f_sub = _font(22)
    if all(value is not None for value in coords):
        coord_str = f"Coords  {coords[0]:.1f} / {coords[1]:.1f} / {coords[2]:.1f} LY"
    else:
        coord_str = "Coords  unavailable"
    draw.text((48, 200), coord_str, font=f_sub, fill=DIM)

    # ── Canonical summary (wrapped) ────────────────────────────────────
    f_rat = _font(28)
    lines = _wrap(summary, max_chars=58)[:3]   # cap at 3 wrapped lines
    y0 = 280
    for i, ln in enumerate(lines):
        prefix = "▸ " if i == 0 else "   "
        draw.text((48, y0 + i * 42), prefix + ln, font=f_rat, fill=FG)

    # ── Footer URL ─────────────────────────────────────────────────────
    f_foot = _font(20)
    draw.text((48, H - 50), f"ED-Finder  ·  /s/{id64}", font=f_foot, fill=DIM)

    # Encode to PNG bytes.
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
