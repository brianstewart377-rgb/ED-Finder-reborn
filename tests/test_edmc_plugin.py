"""ED-Finder's EDMC plugin (apps/edmc-plugin/EDFinder/load.py), "Haul — design",
step 3, and its download (routers/plugin_download.py).

The plugin's own logic is tested without EDMC: what it queues from the
journal, how it groups and sends it, what it does with each answer, how it
reads past journals, and what EDMC's main window shows about the site you are
docked at. EDMC's hooks (the window and the settings tab) need EDMC itself and
are kept thin.

The journal lines are real: EDDI's published samples (tests/fixtures/haul).
The commanders' Frontier IDs are made up, as in tests/test_journal_upload.py.

What must hold: the plugin sends exactly the kinds the site takes, never the
game's beta, and only with a commander's Frontier ID; its batch is one the
site's address accepts; a batch the site could not take now is kept and sent
again, one with a refused key waits for the key to be put right, and one the
site refused is dropped; past journals go with the commander and docking of
their own file; the window shows the game's own figures; and the download
holds the plugin, its licence and its guide.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import zipfile
from pathlib import Path

import pytest

os.environ.setdefault("CORS_ORIGINS", "https://edmc-plugin-test.invalid")

from edfinder_api.haul import readings  # noqa: E402
from edfinder_api.routers import plugin_download, plugin_events  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "apps" / "edmc-plugin" / "EDFinder" / "load.py"
SAMPLES = ROOT / "tests" / "fixtures" / "haul" / "eddi-colonisation-samples.jsonl"
MY_FID, OTHER_FID = "F1000021", "F1000022"


def load_plugin():
    spec = importlib.util.spec_from_file_location("edfinder_edmc_plugin", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plugin = load_plugin()


def samples() -> list[dict]:
    return [json.loads(line) for line in SAMPLES.read_text(encoding="utf-8").splitlines()]


def depot() -> dict:
    return next(event for event in samples() if event["event"] == plugin.DEPOT)


def docked_at(market_id: int) -> dict:
    return {"timestamp": "2025-04-11T08:00:00Z", "event": "Docked", "StationName": "Construction site",
            "StationType": "SpaceConstructionDepot", "SystemAddress": 10477373803, "MarketID": market_id}


class Site:
    """Stands in for ED-Finder's address: answers each post with the next
    status, and keeps what was sent."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.posted: list[tuple[str, dict, str]] = []

    def post(self, url, body, key):
        self.posted.append((url, json.loads(json.dumps(body)), key))
        answer = self.answers.pop(0) if self.answers else 200
        if isinstance(answer, Exception):
            raise answer
        return answer


def sender(site, key="edfp_key", told=None):
    told = told if told is not None else []
    return plugin.Sender(site.post, lambda: (key, "https://ed-finder.test/"), told.append), told


# -- what is sent ---------------------------------------------------------------------------


def test_the_plugin_sends_exactly_the_kinds_the_site_takes():
    assert plugin.KINDS == readings.KINDS
    assert plugin.ADDRESS == plugin_events.router.prefix + "/events"
    assert plugin.BATCH_MAX == plugin_events.MAX_EVENTS


def test_only_kinds_the_site_takes_with_a_commander_and_never_the_beta(monkeypatch):
    site = Site()
    out, _ = sender(site)
    watcher = plugin.Watcher(out)
    lines = samples()
    watcher.entry(MY_FID, {"timestamp": "2025-04-11T07:59:00Z", "event": "FSDJump", "StarSystem": "Sol"})
    watcher.entry(MY_FID, docked_at(lines[-2]["MarketID"]))
    for line in lines:
        watcher.entry(MY_FID, line)
    watcher.entry(MY_FID, lines[0], is_beta=True)
    watcher.entry(None, lines[0])
    watcher.entry("not-a-fid", lines[0])
    queued = [(item[1], item[2]["event"]) for item in out.items]
    assert queued == [(MY_FID, "Docked")] + [(MY_FID, line["event"]) for line in lines]
    # Each event goes with the last docking before it.
    assert out.items[0][3] is None and all(item[3]["event"] == "Docked" for item in list(out.items)[1:])


def test_whose_journal_it_is_is_sent_and_nothing_else_about_the_player():
    out, _ = sender(Site())
    watcher = plugin.Watcher(out)
    watcher.entry(MY_FID, {"timestamp": "2025-04-11T07:00:00Z", "event": "LoadGame", "FID": MY_FID,
                           "Commander": "Test Pilot", "Credits": 123456789, "Loan": 0, "ShipName": "Test"})
    watcher.entry(MY_FID, {"timestamp": "2025-04-11T07:00:01Z", "event": "Commander", "FID": MY_FID,
                           "Name": "Test Pilot"})
    assert [item[2] for item in out.items] == [
        {"timestamp": "2025-04-11T07:00:00Z", "event": "LoadGame", "FID": MY_FID},
        {"timestamp": "2025-04-11T07:00:01Z", "event": "Commander", "FID": MY_FID}]


def test_a_batch_is_one_the_sites_address_accepts():
    items = [(n, MY_FID, line, docked_at(1)) for n, line in enumerate(samples(), start=1)]
    (batch,) = plugin.batches(items)
    body = plugin_events.PluginBatch(**batch.body())
    assert body.fid == MY_FID and body.plugin_version == plugin.VERSION and len(body.events) == len(items)


def test_batches_are_one_commander_and_at_most_500_events_each():
    line = samples()[0]
    items = [(n, MY_FID, line, None) for n in range(1, 502)] + [(502, OTHER_FID, line, docked_at(7))]
    out = plugin.batches(items)
    assert [(batch.fid, len(batch.events), batch.last) for batch in out] == [
        (MY_FID, 500, 500), (MY_FID, 1, 501), (OTHER_FID, 1, 502)]
    assert out[2].body()["docked"]["MarketID"] == 7 and "docked" not in out[0].body()


# -- what is done with each answer ------------------------------------------------------------


def test_sent_events_leave_the_queue_and_the_key_goes_with_them():
    site = Site(200)
    out, told = sender(site)
    for line in samples():
        out.add(MY_FID, line, None)
    out.send_waiting()
    ((url, body, key),) = site.posted
    assert url == "https://ed-finder.test/api/v1/plugin/events" and key == "edfp_key"
    assert len(body["events"]) == 5 and not out.items and told[-1].startswith("Sent 5 events")


def test_a_batch_the_site_could_not_take_now_is_kept_and_sent_again(monkeypatch):
    monkeypatch.setattr(plugin, "RETRY_SECONDS", (0,))
    site = Site(503, ConnectionError("no network"), 200)
    out, told = sender(site)
    out.add(MY_FID, samples()[0], None)
    out.send_waiting()
    assert len(out.items) == 1 and "busy or not ready" in told[-1]
    out.send_waiting()
    assert len(out.items) == 1 and "can't be reached" in told[-1]
    out.send_waiting()
    assert not out.items and len(site.posted) == 3


def test_a_refused_key_keeps_the_events_until_the_key_is_put_right():
    site = Site(401, 200)
    out, told = sender(site)
    out.add(MY_FID, samples()[0], None)
    out.send_waiting()
    assert out.key_refused and len(out.items) == 1 and "did not accept the key" in told[-1]
    out.send_waiting()
    assert len(site.posted) == 1
    out.settings_changed()
    out.send_waiting()
    assert not out.items and len(site.posted) == 2


def test_a_batch_the_site_refused_is_dropped_and_said():
    site = Site(403, 422)
    out, told = sender(site)
    out.add(OTHER_FID, samples()[0], None)
    out.add(MY_FID, samples()[1], None)
    out.send_waiting()
    assert not out.items
    assert "not linked" in told[0] and "refused some events (422)" in told[1]


def test_without_a_key_nothing_is_sent():
    site = Site()
    out, _ = sender(site, key="")
    out.add(MY_FID, samples()[0], None)
    out.send_waiting()
    assert site.posted == [] and len(out.items) == 1


def test_items_dropped_from_a_full_queue_never_remove_unsent_ones(monkeypatch):
    monkeypatch.setattr(plugin, "QUEUE_MAX", 3)
    site = Site(200)
    out, _ = sender(site)
    for line in samples():
        out.add(MY_FID, line, None)
    assert [item[0] for item in out.items] == [3, 4, 5] and out.dropped == 2
    out.send_waiting()
    assert not out.items


# -- past journals ---------------------------------------------------------------------------


def test_past_journals_go_with_their_own_files_commander_and_docking(tmp_path):
    lines = samples()
    mine = [{"timestamp": "2025-04-11T07:00:00Z", "event": "Commander", "FID": MY_FID, "Name": "Test Pilot"},
            docked_at(lines[3]["MarketID"]), *lines[:4]]
    theirs = [{"timestamp": "2025-04-12T07:00:00Z", "event": "LoadGame", "FID": OTHER_FID}, lines[4]]
    nobody = [lines[0]]
    (tmp_path / "Journal.2025-04-11T070000.01.log").write_text("\n".join(map(json.dumps, mine)) + "\n")
    (tmp_path / "Journal.2025-04-12T070000.01.log").write_text("\n".join(map(json.dumps, theirs)) + "\nnot json\n")
    (tmp_path / "Journal.2025-04-10T070000.01.log").write_text("\n".join(map(json.dumps, nobody)) + "\n")
    (tmp_path / "Market.json").write_text("{}")
    out, told = sender(Site())
    assert plugin.queue_past_journals(tmp_path, out, told.append) == len(mine) + len(theirs)
    queued = [(item[1], item[2]["event"], item[3] and item[3]["event"]) for item in out.items]
    assert queued == (
        [(MY_FID, "Commander", None), (MY_FID, "Docked", None)]
        + [(MY_FID, line["event"], "Docked") for line in lines[:4]]
        + [(OTHER_FID, "LoadGame", None), (OTHER_FID, plugin.DEPOT, None)])
    assert told[-1] == "Past journals queued: 8 events from 3 files"


# -- what EDMC's window shows -----------------------------------------------------------------


def test_the_window_shows_the_games_own_figures_for_the_site():
    real = depot()
    first, second = plugin.site_summary(real, "Construction site")
    left = sorted(((good["RequiredAmount"] - good["ProvidedAmount"], good.get("Name_Localised") or "")
                   for good in real["ResourcesRequired"]), key=lambda item: (-item[0], item[1]))
    total = sum(amount for amount, _ in left if amount > 0)
    assert first == "Construction site: %d%% done, %s t still needed" % (
        int(real["ConstructionProgress"] * 100), "{:,}".format(total))
    assert second == " · ".join("%s %s" % (name, "{:,}".format(amount)) for amount, name in left[:3])
    assert plugin.site_summary({**real, "ConstructionComplete": True}, "X") == ("X: finished", "")


def test_the_window_names_the_site_from_the_docking_and_clears_on_leaving():
    out, _ = sender(Site())
    watcher = plugin.Watcher(out)
    real = depot()
    watcher.entry(MY_FID, docked_at(real["MarketID"]))
    assert watcher.entry(MY_FID, real) is True
    assert watcher.site_lines[0].startswith("Construction site: ")
    assert watcher.entry(MY_FID, {"timestamp": "2025-04-11T09:00:00Z", "event": "Undocked"}) is True
    assert watcher.site_lines == ("", "")


# -- the download -----------------------------------------------------------------------------


def test_the_download_holds_the_plugin_its_licence_and_its_guide():
    import asyncio

    plugin_download.plugin_zip.cache_clear()
    answer = asyncio.run(plugin_download.download_plugin())
    assert answer.media_type == "application/zip"
    assert answer.headers["content-disposition"] == 'attachment; filename="EDFinder-plugin-%s.zip"' % plugin.VERSION
    with zipfile.ZipFile(io.BytesIO(answer.body)) as archive:
        assert archive.namelist() == ["EDFinder/load.py", "EDFinder/LICENSE", "EDFinder/README.md"]
        assert archive.read("EDFinder/load.py") == PLUGIN.read_bytes()
        assert archive.read("EDFinder/LICENSE").startswith(b"MIT License")


def test_the_download_says_so_when_the_plugin_is_missing(monkeypatch, tmp_path):
    import asyncio

    from fastapi import HTTPException

    monkeypatch.setattr(plugin_download, "PLACES", (tmp_path / "EDFinder",))
    plugin_download.plugin_zip.cache_clear()
    try:
        with pytest.raises(HTTPException) as refused:
            asyncio.run(plugin_download.download_plugin())
        assert refused.value.status_code == 404
    finally:
        plugin_download.plugin_zip.cache_clear()
