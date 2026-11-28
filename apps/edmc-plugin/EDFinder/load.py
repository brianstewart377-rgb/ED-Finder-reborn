"""ED-Finder's plugin for EDMC (Elite Dangerous Market Connector).

It sends the journal events ED-Finder's Haul page uses to the member's
ED-Finder account, and shows the construction site you are docked at in
EDMC's main window: how far along it is and what it still needs, as the game
itself writes it.

Set up: make a key on ED-Finder's Account page, open EDMC's settings, the
"ED-Finder" tab, and paste the key there. "Test connection" says whether it
works. "Send past journals" sends what is already on this PC.

What is sent, and nothing else: the journal events listed in ``KINDS``, with
your commander's Frontier ID so the site can check the commander is linked to
your account, and the last station you docked at, so a construction site can
be named. Nothing is sent from the game's beta.

The work of sending happens on one background thread, as EDMC's rules for
plugins require; the window is only ever changed from EDMC's main thread.

ED-Finder's own code ("Haul — design", step 3). The way of sending (one
thread, a queue, batches, a key) follows the pattern of techno314/carrier-ops;
no code was copied. MIT licence: see LICENSE.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

VERSION = "0.1.0"
PLUGIN_NAME = "ED-Finder"
DEFAULT_SITE = "https://ed-finder.app"
ADDRESS = "/api/v1/plugin/events"
KEY_SETTING = "edfinder_plugin_key"
SITE_SETTING = "edfinder_plugin_site"

# The kinds of journal event ED-Finder takes. Kept the same as the site's own
# list (apps/api/src/edfinder_api/haul/readings.py); a test checks it.
DEPOT = "ColonisationConstructionDepot"
KINDS = frozenset({
    DEPOT, "Docked",
    "ColonisationContribution", "ColonisationSystemClaim", "ColonisationSystemClaimRelease",
    "ColonisationBeaconDeployed",
    "Loadout", "Cargo", "CargoTransfer", "CarrierStats", "CarrierBuy", "MarketBuy", "MarketSell",
    "Commander", "LoadGame",
})
BATCH_MAX = 500
SEND_EVERY_SECONDS = 5.0
QUEUE_MAX = 20_000
RETRY_SECONDS = (5, 15, 60, 300)
FID = re.compile(r"F[1-9][0-9]{0,19}\Z")
JOURNAL_FILE = re.compile(r"Journal\.[0-9T.\-]+\.log\Z")

plugin_name = os.path.basename(os.path.dirname(os.path.abspath(__file__)))
logger = logging.getLogger("EDMarketConnector.%s" % plugin_name)


# The game's Commander and LoadGame events also carry credits, loans, ship and
# game details. Only these keys of them are sent: enough to say whose journal
# it is.
SLIM = {"Commander": ("timestamp", "event", "FID"), "LoadGame": ("timestamp", "event", "FID")}


def slim(entry: dict) -> dict:
    """The entry as it is sent: Commander and LoadGame cut down to who and when."""
    keep = SLIM.get(entry.get("event"))
    if keep is None:
        return entry
    return {key: entry[key] for key in keep if key in entry}


# -- what is shown about a construction site -----------------------------------------


def good_name(name: Any) -> str:
    """'$aluminium_name;' -> 'aluminium'."""
    text = str(name or "").strip()
    if text.startswith("$") and text.endswith("_name;"):
        text = text[1:-len("_name;")]
    return text


def site_summary(depot: dict, site_name: Optional[str] = None, top: int = 3) -> tuple[str, str]:
    """Two lines for EDMC's window from the game's own list for a site: how far
    along it is and the tonnes still needed, then the goods most needed."""
    goods = []
    for good in depot.get("ResourcesRequired") or []:
        left = int(good.get("RequiredAmount") or 0) - int(good.get("ProvidedAmount") or 0)
        if left > 0:
            label = good.get("Name_Localised") or good_name(good.get("Name"))
            goods.append((left, str(label)))
    goods.sort(key=lambda item: (-item[0], item[1]))
    total = sum(left for left, _ in goods)
    progress = float(depot.get("ConstructionProgress") or 0.0)
    where = site_name or "This site"
    if depot.get("ConstructionComplete"):
        return "%s: finished" % where, ""
    if depot.get("ConstructionFailed"):
        return "%s: failed" % where, ""
    first = "%s: %d%% done, %s t still needed" % (where, int(progress * 100), "{:,}".format(total))
    second = " · ".join("%s %s" % (label, "{:,}".format(left)) for left, label in goods[:top])
    return first, second


# -- sending ------------------------------------------------------------------------


class Batch:
    """Up to ``BATCH_MAX`` events of one commander, with the last station they
    docked at before the first of them."""

    def __init__(self, fid: str, docked: Optional[dict]):
        self.fid = fid
        self.docked = docked
        self.events: list[dict] = []
        self.last = 0

    def body(self) -> dict:
        body = {"fid": self.fid, "plugin_version": VERSION, "events": self.events}
        if self.docked is not None:
            body["docked"] = self.docked
        return body


def batches(items: list[tuple[int, str, dict, Optional[dict]]]) -> list[Batch]:
    """Group queued (number, fid, event, docked-before) items into batches, in
    order, one commander and at most ``BATCH_MAX`` events each."""
    out: list[Batch] = []
    for number, fid, event, docked in items:
        if not out or out[-1].fid != fid or len(out[-1].events) >= BATCH_MAX:
            out.append(Batch(fid, docked))
        out[-1].events.append(event)
        out[-1].last = number
    return out


class Sender:
    """Sends queued events to ED-Finder on its own thread.

    ``post(url, body, key)`` returns the HTTP status (and raises on a network
    failure); ``tell(text)`` reports a status line to the window. Both are
    given in, so the sending can be tested without EDMC or the network.
    """

    def __init__(self, post: Callable[[str, dict, str], int], settings: Callable[[], tuple[str, str]],
                 tell: Callable[[str], None]):
        self.post = post
        self.settings = settings
        self.tell = tell
        self.items: deque = deque()
        self.numbered = 0
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.key_refused = False
        self.dropped = 0
        self.sent = 0
        self.failures = 0
        self.thread: Optional[threading.Thread] = None

    # Called from EDMC's main thread, or from the past-journals thread.
    def add(self, fid: str, event: dict, docked: Optional[dict]) -> None:
        with self.lock:
            if len(self.items) >= QUEUE_MAX:
                self.items.popleft()
                self.dropped += 1
            self.numbered += 1
            self.items.append((self.numbered, fid, event, docked))

    def settings_changed(self) -> None:
        self.key_refused = False
        self.wake.set()

    def start(self) -> None:
        self.thread = threading.Thread(target=self.run, name="ED-Finder sender", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        self.wake.set()

    def run(self) -> None:
        while not self.stopping.is_set():
            self.wake.wait(SEND_EVERY_SECONDS)
            self.wake.clear()
            if not self.stopping.is_set():
                self.send_waiting()

    def send_waiting(self) -> None:
        """Send everything queued, batch by batch. Keeps a batch that could not
        be sent for the next try; drops one the site refused as malformed."""
        key, site = self.settings()
        if not key or self.key_refused:
            return
        with self.lock:
            waiting = list(self.items)
        if not waiting:
            return
        for batch in batches(waiting):
            try:
                status = self.post(site.rstrip("/") + ADDRESS, batch.body(), key)
            except Exception as failure:  # noqa: BLE001 - any network failure means "try later"
                self.failures += 1
                self.tell("ED-Finder can't be reached; will try again")
                logger.info("ED-Finder: sending failed: %s", failure)
                self.wait_before_retry()
                return
            if status in (429, 500, 502, 503, 504):
                self.failures += 1
                self.tell("ED-Finder is busy or not ready; will try again")
                self.wait_before_retry()
                return
            self.failures = 0
            if status == 401:
                # Kept: sent again once the key is put right.
                self.key_refused = True
                self.tell("ED-Finder did not accept the key: check it in Settings")
                return
            self.forget(batch.last)
            if status == 200:
                self.sent += len(batch.events)
                self.tell("Sent %s events · %s" % ("{:,}".format(self.sent), time.strftime("%H:%M")))
            elif status == 403:
                self.tell("This commander is not linked to your ED-Finder account")
            else:
                self.tell("ED-Finder refused some events (%d)" % status)

    def forget(self, last: int) -> None:
        """Take sent items off the queue: everything up to item ``last``."""
        with self.lock:
            while self.items and self.items[0][0] <= last:
                self.items.popleft()

    def wait_before_retry(self) -> None:
        delay = RETRY_SECONDS[min(self.failures, len(RETRY_SECONDS)) - 1]
        self.stopping.wait(delay)


class Watcher:
    """Turns journal entries into queued events and keeps what the window shows."""

    def __init__(self, sender: Sender):
        self.sender = sender
        self.docked: Optional[dict] = None
        self.site_lines: tuple[str, str] = ("", "")

    def entry(self, fid: Optional[str], entry: dict, is_beta: bool = False) -> bool:
        """Queue one journal entry if ED-Finder takes it. True when the site
        summary changed."""
        if is_beta or not isinstance(entry, dict):
            return False
        kind = entry.get("event")
        changed = False
        if kind in KINDS and fid and FID.fullmatch(fid):
            self.sender.add(fid, slim(entry), self.docked)
        if kind == "Docked":
            self.docked = dict(entry)
            if "ConstructionDepot" not in str(entry.get("StationType") or ""):
                changed = self.site_lines != ("", "")
                self.site_lines = ("", "")
        elif kind == "Undocked":
            changed = self.site_lines != ("", "")
            self.site_lines = ("", "")
        elif kind == DEPOT:
            name = None
            if self.docked is not None and self.docked.get("MarketID") == entry.get("MarketID"):
                name = self.docked.get("StationName")
            lines = site_summary(entry, name)
            changed = lines != self.site_lines
            self.site_lines = lines
        return changed


def journal_files(folder: Path) -> list[Path]:
    """The game's journal files in a folder, oldest first (their names sort by time)."""
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir() if JOURNAL_FILE.fullmatch(path.name))


def queue_past_journals(folder: Path, sender: Sender, tell: Callable[[str], None]) -> int:
    """Queue the events ED-Finder takes from every journal file in the folder,
    each with the commander and docking of its own file. Returns how many."""
    count = 0
    files = journal_files(folder)
    for number, path in enumerate(files, start=1):
        fid: Optional[str] = None
        docked: Optional[dict] = None
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as failure:
            logger.info("ED-Finder: could not read %s: %s", path.name, failure)
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            kind = entry.get("event")
            if kind in ("Commander", "LoadGame") and isinstance(entry.get("FID"), str):
                fid = entry["FID"]
            if kind in KINDS and fid and FID.fullmatch(fid):
                sender.add(fid, slim(entry), docked)
                count += 1
            if kind == "Docked":
                docked = entry
        if number % 20 == 0:
            tell("Reading past journals: %d of %d files" % (number, len(files)))
    tell("Past journals queued: %s events from %d files" % ("{:,}".format(count), len(files)))
    sender.wake.set()
    return count


# -- EDMC's hooks ------------------------------------------------------------------------
# Everything below needs EDMC; everything above can be tested without it.

_sender: Optional[Sender] = None
_watcher: Optional[Watcher] = None
_status: "queue.Queue[str]" = queue.Queue()
_ui: dict = {}
_session: Any = None


def _config():
    from config import config  # EDMC's own; only for this plugin's settings
    return config


def _settings() -> tuple[str, str]:
    config = _config()
    key = (config.get_str(KEY_SETTING) or "").strip()
    site = (config.get_str(SITE_SETTING) or "").strip() or DEFAULT_SITE
    return key, site


def _post(url: str, body: dict, key: str) -> int:
    global _session
    if _session is None:
        import timeout_session  # EDMC's: requests with a time limit and EDMC's user agent
        _session = timeout_session.new_session()
    answer = _session.post(url, json=body, headers={"Authorization": "Bearer %s" % key})
    return int(answer.status_code)


def _tell(text: str) -> None:
    """Report a status line from any thread; the window shows it on EDMC's main thread."""
    _status.put(text)
    frame = _ui.get("frame")
    if frame is not None and not _config().shutting_down:
        try:
            frame.event_generate("<<EDFinderStatus>>", when="tail")
        except Exception as failure:  # noqa: BLE001 - the window may be closing
            logger.debug("ED-Finder: could not update the window: %s", failure)


def _show_status(_event: Any = None) -> None:
    text = None
    while not _status.empty():
        text = _status.get_nowait()
    if text is not None and _ui.get("status") is not None:
        _ui["status"]["text"] = text


def _show_site() -> None:
    if _watcher is None or _ui.get("site") is None:
        return
    first, second = _watcher.site_lines
    _ui["site"]["text"] = first
    _ui["goods"]["text"] = second


def plugin_start3(plugin_dir: str) -> str:
    global _sender, _watcher
    _sender = Sender(_post, _settings, _tell)
    _watcher = Watcher(_sender)
    _sender.start()
    return PLUGIN_NAME


def plugin_stop() -> None:
    if _sender is not None:
        _sender.stop()


def plugin_app(parent: Any) -> Any:
    import tkinter as tk
    frame = tk.Frame(parent)
    frame.columnconfigure(1, weight=1)
    tk.Label(frame, text="ED-Finder:").grid(row=0, column=0, sticky=tk.W)
    _ui["status"] = tk.Label(frame, text="Waiting for the game" if _settings()[0] else "Add your key in Settings",
                             anchor=tk.W)
    _ui["status"].grid(row=0, column=1, sticky=tk.EW)
    _ui["site"] = tk.Label(frame, text="", anchor=tk.W, justify=tk.LEFT)
    _ui["site"].grid(row=1, column=0, columnspan=2, sticky=tk.EW)
    _ui["goods"] = tk.Label(frame, text="", anchor=tk.W, justify=tk.LEFT, wraplength=320)
    _ui["goods"].grid(row=2, column=0, columnspan=2, sticky=tk.EW)
    _ui["frame"] = frame
    frame.bind("<<EDFinderStatus>>", _show_status)
    return frame


def plugin_prefs(parent: Any, cmdr: str, is_beta: bool) -> Any:
    import tkinter as tk
    import myNotebook as nb  # EDMC's
    frame = nb.Frame(parent)
    frame.columnconfigure(1, weight=1)
    key, site = _settings()
    _ui["key_var"] = tk.StringVar(value=key)
    _ui["site_var"] = tk.StringVar(value=site)
    nb.Label(frame, text="ED-Finder %s: sends your construction sites' lists to your ED-Finder account."
             % VERSION).grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=10, pady=(10, 4))
    nb.Label(frame, text="Key (from ED-Finder's Account page)").grid(row=1, column=0, sticky=tk.W, padx=10)
    nb.EntryMenu(frame, textvariable=_ui["key_var"], show="*").grid(row=1, column=1, sticky=tk.EW, padx=10)
    nb.Label(frame, text="ED-Finder's address").grid(row=2, column=0, sticky=tk.W, padx=10)
    nb.EntryMenu(frame, textvariable=_ui["site_var"]).grid(row=2, column=1, sticky=tk.EW, padx=10)
    _ui["prefs_status"] = nb.Label(frame, text="")
    nb.Button(frame, text="Test connection", command=lambda: _test_connection(cmdr)).grid(
        row=3, column=0, sticky=tk.W, padx=10, pady=6)
    nb.Button(frame, text="Send past journals", command=_send_past_journals).grid(
        row=3, column=1, sticky=tk.W, padx=10, pady=6)
    _ui["prefs_status"].grid(row=4, column=0, columnspan=2, sticky=tk.W, padx=10)
    return frame


def prefs_changed(cmdr: str, is_beta: bool) -> None:
    config = _config()
    if _ui.get("key_var") is not None:
        config.set(KEY_SETTING, _ui["key_var"].get().strip())
        config.set(SITE_SETTING, _ui["site_var"].get().strip() or DEFAULT_SITE)
    if _sender is not None:
        _sender.settings_changed()


def _say_in_prefs(text: str) -> None:
    label = _ui.get("prefs_status")
    if label is not None:
        label["text"] = text


def _test_connection(cmdr: str) -> None:
    key = _ui["key_var"].get().strip() if _ui.get("key_var") else ""
    site = (_ui["site_var"].get().strip() if _ui.get("site_var") else "") or DEFAULT_SITE
    fid = _last_fid.get("fid")
    if not key:
        _say_in_prefs("Paste your key first.")
        return
    if not fid:
        _say_in_prefs("Start the game first, so the plugin knows your commander.")
        return
    _say_in_prefs("Testing…")

    def run() -> None:
        try:
            status = _post(site.rstrip("/") + ADDRESS, {"fid": fid, "plugin_version": VERSION, "events": []}, key)
        except Exception as failure:  # noqa: BLE001
            logger.info("ED-Finder: test failed: %s", failure)
            _tell("ED-Finder can't be reached")
            return
        _tell({200: "Connected to ED-Finder", 401: "ED-Finder did not accept the key",
               403: "This commander is not linked to your ED-Finder account",
               503: "ED-Finder is not ready for the plugin yet"}.get(status, "ED-Finder answered %d" % status))

    threading.Thread(target=run, name="ED-Finder test", daemon=True).start()
    _say_in_prefs("Testing… the answer appears in EDMC's main window.")


def _send_past_journals() -> None:
    if _sender is None:
        return
    config = _config()
    folder = Path(config.get_str("journaldir") or config.default_journal_dir)
    _say_in_prefs("Reading past journals… progress appears in EDMC's main window.")
    threading.Thread(target=queue_past_journals, args=(folder, _sender, _tell),
                     name="ED-Finder past journals", daemon=True).start()


_last_fid: dict = {}


def journal_entry(cmdr: str, is_beta: bool, system: Optional[str], station: Optional[str],
                  entry: dict, state: dict) -> Optional[str]:
    if _watcher is None:
        return None
    fid = state.get("FID") if isinstance(state, dict) else None
    if fid:
        _last_fid["fid"] = fid
    if _watcher.entry(fid, entry, is_beta):
        _show_site()
    if entry.get("event") == DEPOT and _sender is not None:
        _sender.wake.set()
    return None
