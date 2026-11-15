"""Things that used to go wrong without a word now say so.

The code review of 15 November 2026 found places where the running site could
fail and leave no trace: a cache that could not be reached, a body whose
stored rings could not be read, a sign-in that lost its commander name, a
journal import that failed for one reason and reported another. Each now
leaves a line in the log, and the cache failures are counted as well.

It also found the opposite fault, saying too much: a watchlist address
carries the browser's own key, and it was written to the logs whole; and the
health check sent the database's own words to the browser.

No database is needed here. The web server's half of the log rule is checked
with the real web server in tests/test_deploy_docker.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx
import pytest
from fastapi import HTTPException

os.environ.setdefault("CORS_ORIGINS", "https://quiet-failures-test.invalid")

from edfinder_api import deps, local_search, request_logging  # noqa: E402
from edfinder_api.journal_import import store as journal_store  # noqa: E402
from edfinder_api.routers import auth as auth_route  # noqa: E402
from edfinder_api.routers import meta as meta_route  # noqa: E402
from edfinder_api.state import metrics  # noqa: E402

# Made up for these tests; no browser holds this key.
KEY = "0b7d3c5e-made-up-for-the-test"


def run(coroutine):
    return asyncio.run(coroutine)


# ── The cache ──────────────────────────────────────────────────────────────

class _CacheAway:
    """A cache that refuses every read and write, as when it cannot be reached."""

    async def get(self, key):
        raise ConnectionError("the cache is away")

    async def setex(self, key, ttl, value):
        raise ConnectionError("the cache is away")


class _CacheHolding:
    def __init__(self, held: str) -> None:
        self.held = held

    async def get(self, key):
        return self.held


@pytest.fixture
def cache_counts(monkeypatch):
    """The cache's counts from nought, and the once-a-minute rule from the start."""
    for name in ("cache_hits", "cache_misses", "cache_errors"):
        monkeypatch.setitem(metrics, name, 0)
    monkeypatch.setattr(deps, "_last_cache_warning", None)
    return metrics


def test_a_cache_that_is_away_is_a_miss_that_is_counted_and_said(cache_counts, caplog):
    caplog.set_level(logging.WARNING, logger="ed_finder")
    assert run(deps.cache_get("find:count:abc", _CacheAway())) is None
    run(deps.cache_set("find:count:abc", {"total": 12}, 60, _CacheAway()))

    # The caller is told "not kept", as before: the cache is never needed for
    # a right answer. But the failures no longer look like ordinary misses.
    assert cache_counts["cache_misses"] == 1 and cache_counts["cache_hits"] == 0
    assert cache_counts["cache_errors"] == 2
    said = [record.getMessage() for record in caplog.records]
    assert len(said) == 1, "two failures within a minute are one line in the log, not two"
    assert "the cache could not be read (ConnectionError)" in said[0]
    assert "find:count:abc" not in said[0], "a key can be made from what a person searched for"


def test_the_log_is_told_again_once_a_minute_has_passed(cache_counts, caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger="ed_finder")
    now = [1000.0]
    monkeypatch.setattr(deps.time, "monotonic", lambda: now[0])
    run(deps.cache_get("k", _CacheAway()))
    now[0] += 59
    run(deps.cache_get("k", _CacheAway()))
    assert len(caplog.records) == 1
    now[0] += 2
    run(deps.cache_set("k", 1, 60, _CacheAway()))
    assert len(caplog.records) == 2
    assert "could not be written" in caplog.records[1].getMessage()
    assert "3 cache failures since the API started" in caplog.records[1].getMessage()


def test_kept_text_that_cannot_be_read_is_not_counted_as_found(cache_counts, caplog):
    caplog.set_level(logging.WARNING, logger="ed_finder")
    assert run(deps.cache_get("k", _CacheHolding("{not json"))) is None
    assert cache_counts["cache_hits"] == 0, "it used to be counted as found and as missed, both"
    assert cache_counts["cache_misses"] == 1 and cache_counts["cache_errors"] == 1


def test_a_working_cache_counts_no_failures(cache_counts, caplog):
    caplog.set_level(logging.WARNING, logger="ed_finder")
    assert run(deps.cache_get("k", _CacheHolding(json.dumps({"total": 7})))) == {"total": 7}
    assert cache_counts == {**cache_counts, "cache_hits": 1, "cache_misses": 0, "cache_errors": 0}
    assert not caplog.records


def test_the_count_of_cache_failures_is_on_the_metrics_page(cache_counts):
    cache_counts["cache_errors"] = 4
    page = run(meta_route.metrics_endpoint())
    text = page if isinstance(page, str) else page.body.decode()
    assert "ed_finder_cache_errors_total 4" in text.splitlines()


# ── A body's rings and signals ─────────────────────────────────────────────

def _body_row(**changes):
    # The columns _normalize_body reads, with a ringed gas giant's values.
    row = {"id": 4521, "name": "A 5", "ring_types": '["Icy", "Rocky"]',
           "signals": '[{"name": "Biological", "count": 2}]', "has_signals": True}
    row.update(changes)
    return row


def test_a_bodys_stored_rings_and_signals_are_read(caplog):
    caplog.set_level(logging.WARNING, logger=local_search.log.name)
    body = local_search._normalize_body(_body_row())
    assert body["rings"] == [{"type": "Icy"}, {"type": "Rocky"}]
    assert body["signals"] == [{"name": "Biological", "count": 2}]
    assert not caplog.records


def test_rings_that_cannot_be_read_are_shown_as_none_and_the_log_names_the_body(caplog):
    caplog.set_level(logging.WARNING, logger=local_search.log.name)
    body = local_search._normalize_body(_body_row(ring_types='["Icy", '))
    assert body["rings"] == [], "the page is still answered"
    assert body["signals"] == [{"name": "Biological", "count": 2}], "the readable half is kept"
    said = [record.getMessage() for record in caplog.records]
    assert said == ["body 4521: its stored ring_types could not be read, so it is shown with none"]


def test_a_body_with_no_rings_says_nothing(caplog):
    caplog.set_level(logging.WARNING, logger=local_search.log.name)
    body = local_search._normalize_body(_body_row(ring_types=None, signals=[], has_signals=False))
    assert body["rings"] == [] and body["signals"] == []
    assert not caplog.records


# ── The commander name at sign-in ──────────────────────────────────────────

class _Frontier:
    """Frontier's servers, as far as a sign-in goes: the token, the account
    and the game profile. Only the profile's answer changes between tests."""

    def __init__(self, profile) -> None:
        self.profile = profile

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "token_type": "Bearer"})
        if path.endswith("/decode"):
            return httpx.Response(200, json={
                "iss": auth_route.FRONTIER_ISSUER,
                "usr": {"customer_id": "1234567", "platform": "frontier"}})
        if path.endswith("/me"):
            return httpx.Response(200, json={"customer_id": "1234567", "platform": "frontier"})
        if path.endswith("/profile"):
            if isinstance(self.profile, Exception):
                raise self.profile
            return self.profile
        return httpx.Response(404)


@pytest.fixture
def frontier(monkeypatch):
    real_client = httpx.AsyncClient

    def install(profile):
        handler = _Frontier(profile)
        monkeypatch.setattr(
            auth_route.httpx, "AsyncClient",
            lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    return install


def _sign_in():
    return run(auth_route._exchange_frontier_code("code", "verifier"))


def test_a_sign_in_whose_name_lookup_fails_goes_ahead_and_the_log_says_why(frontier, caplog):
    caplog.set_level(logging.WARNING, logger="ed_finder")
    frontier(httpx.ConnectTimeout("Frontier's game server did not answer"))
    identity = _sign_in()
    assert identity, "the sign-in is not held up by the name"
    said = [record.getMessage() for record in caplog.records]
    assert said == ["Frontier profile lookup failed (ConnectTimeout); signing in with no commander name"]


def test_a_sign_in_whose_name_lookup_is_refused_says_the_status(frontier, caplog):
    caplog.set_level(logging.WARNING, logger="ed_finder")
    frontier(httpx.Response(503))
    assert _sign_in()
    said = [record.getMessage() for record in caplog.records]
    assert said == ["Frontier profile lookup answered 503; signing in with no commander name"]


def test_a_sign_in_that_gets_its_name_says_nothing(frontier, caplog):
    caplog.set_level(logging.WARNING, logger="ed_finder")
    frontier(httpx.Response(200, json={"commander": {"name": "A Commander"}}))
    assert _sign_in()
    assert not caplog.records


# ── A journal import that cannot mark its systems ──────────────────────────

class _RefusingConnection:
    """A database connection whose first statement fails."""

    def __init__(self) -> None:
        self.asked = 0

    async def execute(self, *args):
        self.asked += 1
        raise TimeoutError("the statement ran out of time")

    async def fetchrow(self, *args):
        self.asked += 1
        raise AssertionError("nothing may be asked after a statement has failed")


def test_an_import_that_cannot_mark_its_systems_fails_with_the_real_reason(caplog):
    caplog.set_level(logging.WARNING, logger="ed_finder")
    connection = _RefusingConnection()
    with pytest.raises(TimeoutError):
        run(journal_store._mark_dirty_systems_incremental(connection, {11, 12, 13}, batch_size=2))
    # One statement, then stop: inside a transaction PostgreSQL refuses the
    # rest anyway, and the import used to fail later with "transaction is
    # aborted", which named nothing.
    assert connection.asked == 1
    said = [record.getMessage() for record in caplog.records]
    assert said == ["marking 2 systems for new scores failed (TimeoutError); the import cannot go on"]


# ── What the logs and the browser are not told ─────────────────────────────

@pytest.mark.parametrize("asked, written", [
    ("/api/v2/watchlist/" + KEY, "/api/v2/watchlist/(hidden)"),
    ("/api/v2/watchlist/" + KEY + "/8216113749", "/api/v2/watchlist/(hidden)/8216113749"),
    ("/api/v2/watchlist/" + KEY + "/changes?since=5", "/api/v2/watchlist/(hidden)/changes?since=5"),
    ("https://ed-finder.app/api/v2/watchlist/" + KEY, "https://ed-finder.app/api/v2/watchlist/(hidden)"),
    # Frontier's one-time code is in the query, so the query goes.
    ("/api/v1/auth/frontier/callback?code=abc&state=def", "/api/v1/auth/frontier/callback"),
    # Every other address is written as it was asked.
    ("/api/local/search?x=1", "/api/local/search?x=1"),
    ("/api/watchlist/changes", "/api/watchlist/changes"),
    ("/api/v2/watchlist", "/api/v2/watchlist"),
])
def test_what_is_written_in_place_of_an_address(asked, written):
    assert request_logging.redact_request_target(asked) == written


def test_the_apis_access_log_hides_the_watchlist_key():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/api/v2/watchlist/" + KEY + "/changes", "1.1", 200), None)
    assert request_logging.FrontierOAuthAccessLogFilter().filter(record) is True
    line = record.getMessage()
    assert KEY not in line
    assert '"GET /api/v2/watchlist/(hidden)/changes HTTP/1.1" 200' in line


def test_an_error_report_hides_the_watchlist_key_and_keeps_the_rest():
    event = {"request": {
        "url": "https://ed-finder.app/api/v2/watchlist/" + KEY,
        "query_string": "since=5",
        "env": {"REQUEST_URI": "/api/v2/watchlist/" + KEY + "?since=5", "REMOTE_ADDR": "203.0.113.9"},
    }}
    sent = request_logging.redact_frontier_oauth_sentry_event(event, {})
    assert KEY not in json.dumps(sent)
    assert sent["request"]["url"] == "https://ed-finder.app/api/v2/watchlist/(hidden)"
    assert sent["request"]["env"]["REQUEST_URI"] == "/api/v2/watchlist/(hidden)?since=5"
    assert sent["request"]["query_string"] == "since=5", "only the key is taken out"


class _PoolThatCannotConnect:
    def acquire(self):
        raise OSError("connection to edfinder-postgres:5432 (schema v3_state) refused")


def test_the_health_check_tells_the_browser_no_more_than_it_needs(caplog):
    caplog.set_level(logging.WARNING, logger="ed_finder")
    with pytest.raises(HTTPException) as refused:
        run(meta_route.health(_PoolThatCannotConnect()))
    assert refused.value.status_code == 503
    assert refused.value.detail == "the database could not be reached"
    # The reason is still to be had: it is in the log, where the owner reads it.
    assert any("edfinder-postgres:5432" in record.getMessage() for record in caplog.records)
