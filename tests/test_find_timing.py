"""scripts/pi/find_timing.py: the Find timing script run inside the API container."""
from __future__ import annotations

import asyncio
import importlib.util
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("CORS_ORIGINS", "https://find-timing-test.invalid")

from tests.test_find_reach import reach_database  # noqa: E402,F401  (the shared fixture)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pi" / "find_timing.py"


def _load():
    spec = importlib.util.spec_from_file_location("find_timing", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_every_search_it_times_is_one_the_api_accepts():
    from edfinder_api.local_search import prepare_local_search_v3
    from edfinder_api.ranking.profile import ARCHETYPE_KEYS

    timing = _load()
    found = timing.scenarios(ARCHETYPE_KEYS[0])
    assert len(found) == 8
    for _, body in found:
        if body is not None:
            prepare_local_search_v3(dict(body))
    # Only public places, and nothing of a user's.
    text = SCRIPT.read_text(encoding="utf-8")
    assert "Sol" in text and "Colonia" in text


@pytest.mark.db
def test_it_times_every_search_on_a_real_database(reach_database, monkeypatch, capsys):
    from urllib.parse import urlsplit, urlunsplit

    from edfinder_api.config import settings

    url = urlsplit(os.environ["RATINGS_V4_VALIDATION_DATABASE_URL"])
    test_url = urlunsplit(url._replace(path="/" + reach_database.info.dbname))
    monkeypatch.setattr(settings, "database_url", test_url)
    monkeypatch.setattr(settings, "database_readonly_url", None)

    assert asyncio.run(_load().main()) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[0].startswith("Database: jit=")
    rows = [line for line in printed if re.match(r"^(Any|Type), ", line)]
    assert len(rows) == 8
    for row in rows:
        assert "FAILED" not in row, row
        assert len(re.findall(r"\d+ms", row)) == 3, row
        assert row.rstrip().endswith("found"), row
