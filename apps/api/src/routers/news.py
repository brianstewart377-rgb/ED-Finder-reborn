"""Official Elite Dangerous news surface.

This route proxies the latest headlines from the official Elite Dangerous
news page so the frontend can render a stable banner without relying on
browser-side scraping or cross-origin fetches.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from defusedxml import ElementTree

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends

from edfinder_api.deps import cache_get, cache_set, get_redis

log = logging.getLogger('ed_finder')

router = APIRouter(tags=['news'])

ELITE_NEWS_URL = 'https://www.elitedangerous.com/news'
ELITE_GALNET_URL = 'https://www.elitedangerous.com/en-US/Galnet'
ELITE_GALNET_RSS_URL = 'https://community.elitedangerous.com/galnet-rss'
ELITE_NEWS_CACHE_TTL = 900
ELITE_NEWS_FALLBACK_TTL = 60
_DATE_ONLY_RE = re.compile(r'^\d{1,2}\s+[A-Za-z]+\s+\d{4}$|^[A-Za-z]+\s+\d{1,2},\s+\d{4}$|^\d{1,2}\s+[A-Za-z]+\s+\d{4,}$')
_LOCALE_NEWS_PATH_RE = re.compile(r'^/[A-Za-z]{2}-[A-Za-z]{2}(?=/news(?:/|$))')
_LOCAL_CACHE: dict[tuple[int], tuple[float, dict]] = {}
_FALLBACK_ITEMS = [
    {
        'title': 'Open official Elite Dangerous news',
        'url': ELITE_NEWS_URL,
        'source': 'news',
    },
    {
        'title': 'Open official Galnet',
        'url': ELITE_GALNET_URL,
        'source': 'galnet',
    },
]


class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != 'a':
            return
        href = dict(attrs).get('href')
        if not href:
            return
        self._href = href
        self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != 'a' or self._href is None:
            return
        text = ' '.join(part.strip() for part in self._chunks if part.strip())
        if text:
            self.links.append((self._href, text))
        self._href = None
        self._chunks = []


def _normalise_news_url(href: str) -> str | None:
    absolute = urljoin(ELITE_NEWS_URL, href)
    parsed = urlparse(absolute)
    if parsed.netloc not in ('www.elitedangerous.com', 'elitedangerous.com'):
        return None
    path = _LOCALE_NEWS_PATH_RE.sub('', parsed.path)
    if not path.startswith('/news/'):
        return None
    if path.rstrip('/') == '/news':
        return None
    if 'page=' in parsed.query:
        return None
    return f'https://www.elitedangerous.com{path}'


def _looks_like_title(text: str) -> bool:
    collapsed = ' '.join(text.split())
    if len(collapsed) < 8:
        return False
    lower = collapsed.lower()
    if lower in {'news', 'galnet', 'update notes', 'community update', 'dlc'}:
        return False
    if _DATE_ONLY_RE.match(collapsed):
        return False
    return any(char.isalpha() for char in collapsed)


def extract_elite_news_items(html: str, *, limit: int = 8) -> list[dict[str, str]]:
    collector = _AnchorCollector()
    collector.feed(html)

    grouped: OrderedDict[str, list[str]] = OrderedDict()
    for href, text in collector.links:
        news_url = _normalise_news_url(href)
        if news_url is None:
            continue
        grouped.setdefault(news_url, []).append(' '.join(text.split()))

    items: list[dict[str, str]] = []
    for url, texts in grouped.items():
        title = next((text for text in texts if _looks_like_title(text)), None)
        if title is None:
            continue
        source = 'galnet' if '/news/galnet/' in url else 'news'
        items.append({
            'title': title,
            'url': url,
            'source': source,
        })
        if len(items) >= limit:
            break

    return items


def extract_galnet_rss_items(xml_text: str, *, limit: int = 8) -> list[dict[str, str]]:
    root = ElementTree.fromstring(xml_text)
    items: list[dict[str, str]] = []

    for item in root.findall('./channel/item'):
        title = (item.findtext('title') or '').strip()
        link = (item.findtext('link') or '').strip() or ELITE_GALNET_URL
        if not title:
            continue
        items.append({
            'title': title,
            'url': link,
            'source': 'galnet',
        })
        if len(items) >= limit:
            break

    return items


def _fetch_galnet_rss(limit: int) -> dict:
    request = Request(
        ELITE_GALNET_RSS_URL,
        headers={
            'User-Agent': 'ED-Finder/3.x (+https://ed-finder.app)',
            'Accept': 'application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.1',
        },
    )
    with urlopen(request, timeout=10) as response:  # nosemgrep: dynamic-urllib-use-detected -- ELITE_GALNET_RSS_URL is a hardcoded constant, not user input
        xml_text = response.read().decode('utf-8', errors='replace')

    items = extract_galnet_rss_items(xml_text, limit=limit)
    if not items:
        raise ValueError('Official Elite Dangerous Galnet RSS returned no parseable items')

    return {
        'items': items,
        'source_url': ELITE_GALNET_RSS_URL,
        'fetched_at': datetime.now(timezone.utc).isoformat(),
        'stale': False,
    }


def _fetch_official_news(limit: int) -> dict:
    request = Request(
        ELITE_NEWS_URL,
        headers={
            'User-Agent': 'ED-Finder/3.x (+https://ed-finder.app)',
            'Accept': 'text/html,application/xhtml+xml',
        },
    )
    with urlopen(request, timeout=10) as response:  # nosemgrep: dynamic-urllib-use-detected -- ELITE_NEWS_URL is a hardcoded constant, not user input
        html = response.read().decode('utf-8', errors='replace')

    items = extract_elite_news_items(html, limit=limit)
    if items:
        return {
            'items': items,
            'source_url': ELITE_NEWS_URL,
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'stale': False,
        }

    return _fetch_galnet_rss(limit)


def _fallback_payload(limit: int) -> dict:
    return {
        'items': _FALLBACK_ITEMS[:limit],
        'source_url': ELITE_NEWS_URL,
        'fetched_at': datetime.now(timezone.utc).isoformat(),
        'stale': True,
    }


def _is_fallback_payload(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    items = payload.get('items')
    if not isinstance(items, list) or not items:
        return False
    fallback_pairs = {(item['title'], item['url']) for item in _FALLBACK_ITEMS}
    item_pairs = {
        (item.get('title'), item.get('url'))
        for item in items
        if isinstance(item, dict)
    }
    return item_pairs.issubset(fallback_pairs)


@router.get('/api/news/latest')
async def latest_news(
    limit: int = 8,
    redis: Optional[aioredis.Redis] = Depends(get_redis),
):
    limit = max(1, min(limit, 12))
    cache_key = f'elite-news:latest:{limit}'

    cached = await cache_get(cache_key, redis)
    if cached and not _is_fallback_payload(cached):
        return cached

    local_key = (limit,)
    now = time.time()
    cached_local = _LOCAL_CACHE.get(local_key)
    if cached_local and cached_local[0] > now and not _is_fallback_payload(cached_local[1]):
        return cached_local[1]

    try:
        payload = await asyncio.to_thread(_fetch_official_news, limit)
        _LOCAL_CACHE[local_key] = (now + ELITE_NEWS_CACHE_TTL, payload)
        await cache_set(cache_key, payload, ELITE_NEWS_CACHE_TTL, redis)
        return payload
    except Exception as exc:
        log.warning('Official Elite Dangerous news fetch failed: %s', exc)
        if cached_local:
            stale_payload = {**cached_local[1], 'stale': True}
            return stale_payload
        if cached:
            stale_payload = {**cached, 'stale': True}
            _LOCAL_CACHE[local_key] = (now + ELITE_NEWS_FALLBACK_TTL, stale_payload)
            return stale_payload
        fallback_payload = _fallback_payload(limit)
        _LOCAL_CACHE[local_key] = (now + ELITE_NEWS_FALLBACK_TTL, fallback_payload)
        await cache_set(cache_key, fallback_payload, ELITE_NEWS_FALLBACK_TTL, redis)
        return fallback_payload
