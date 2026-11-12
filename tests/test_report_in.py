"""Telling healthchecks.io how a nightly job ended (deploy/report_in.py).

Nothing here touches the network or the system's log: the one function that
fetches a web address, and the one that reads the log, are swapped for fakes
that record what they were given.
"""
from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
import urllib.error

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADDRESS = "https://hc-ping.com/0f3c4d5e-1111-2222-3333-444455556666"
SYSTEMD_WORKED = {"SERVICE_RESULT": "success", "EXIT_CODE": "exited", "EXIT_STATUS": "0",
                  "INVOCATION_ID": "0123456789abcdef0123456789abcdef"}
SYSTEMD_FAILED = {"SERVICE_RESULT": "exit-code", "EXIT_CODE": "exited", "EXIT_STATUS": "1",
                  "INVOCATION_ID": "0123456789abcdef0123456789abcdef"}


def load():
    spec = importlib.util.spec_from_file_location("report_in_under_test", ROOT / "deploy" / "report_in.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Answer:
    def __init__(self, status: int = 200) -> None:
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


class Web:
    """Stands in for urllib.request.urlopen: records each request, and answers
    with what the test queued (an HTTP status, or an exception to raise)."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, bytes]] = []
        self.answers: list[object] = []

    def __call__(self, request, timeout=None):
        assert timeout is not None, "a fetch with no time limit could hold the job's unit open"
        self.requests.append((request.get_method(), request.full_url, request.data))
        answer = self.answers.pop(0) if self.answers else 200
        if isinstance(answer, BaseException):
            raise answer
        return Answer(answer)


@pytest.fixture
def tool(tmp_path, monkeypatch):
    module = load()
    web = Web()
    monkeypatch.setattr(module, "REPORT_DIR", tmp_path / "report")
    monkeypatch.setattr(module, "FILE_OWNER", os.getuid())
    monkeypatch.setattr(module, "PAUSE_SECONDS", 0.0)
    monkeypatch.setattr(module.urllib.request, "urlopen", web)
    monkeypatch.setattr(module, "last_output", lambda environ: "FAILED: not enough free disk space")
    (tmp_path / "report").mkdir()
    module.web = web
    return module


def save(tool, job: str, text: str = ADDRESS + "\n", mode: int = 0o600) -> Path:
    path = tool.REPORT_DIR / (job + ".url")
    path.write_text(text, encoding="ascii")
    path.chmod(mode)
    return path


def run(tool, capsys, *argv: str, environ: dict | None = None) -> tuple[int, str]:
    code = tool.main(list(argv), environ=SYSTEMD_WORKED if environ is None else environ)
    out = capsys.readouterr().out
    assert "hc-ping.com" not in out and "0f3c4d5e" not in out, "the address is a secret: never printed"
    return code, out


# -- a job that worked, and one that did not ------------------------------------------


def test_a_job_that_worked_fetches_the_address_as_it_is(tool, capsys):
    save(tool, "galaxy-update")
    code, out = run(tool, capsys, "galaxy-update")
    assert code == 0
    assert tool.web.requests == [("POST", ADDRESS, b"galaxy-update: worked")]
    assert out == "Told healthchecks.io that the galaxy-update job worked.\n"


def test_a_job_that_failed_fetches_the_fail_address_and_sends_its_last_lines(tool, capsys):
    save(tool, "state-refresh")
    code, out = run(tool, capsys, "state-refresh", environ=SYSTEMD_FAILED)
    assert code == 0
    assert tool.web.requests == [("POST", ADDRESS + "/fail", (
        b"state-refresh: FAILED (it ended with code 1)\n\n"
        b"The last lines it printed:\nFAILED: not enough free disk space"))]
    assert out == "Told healthchecks.io that the state-refresh job FAILED (it ended with code 1).\n"


@pytest.mark.parametrize("systemd, words", [
    ({"SERVICE_RESULT": "timeout", "EXIT_STATUS": "TERM"}, "it ran for longer than it is allowed and was stopped"),
    ({"SERVICE_RESULT": "oom-kill", "EXIT_STATUS": "KILL"}, "the machine ran out of memory"),
    ({"SERVICE_RESULT": "signal", "EXIT_STATUS": "TERM"}, "it was stopped from outside, signal TERM"),
    ({"SERVICE_RESULT": "core-dump", "EXIT_STATUS": "SEGV"}, "systemd says: core-dump"),
    ({"SERVICE_RESULT": ""}, "systemd says: nothing"),
])
def test_every_way_of_not_working_is_a_failure_said_in_plain_words(tool, capsys, systemd, words):
    save(tool, "galaxy-update")
    code, out = run(tool, capsys, "galaxy-update", environ=systemd)
    assert code == 0
    method, address, body = tool.web.requests[0]
    assert address == ADDRESS + "/fail" and body.startswith(b"galaxy-update: FAILED (" + words.encode())
    assert "FAILED (%s)" % words in out


def test_a_failure_is_still_reported_when_its_last_lines_cannot_be_had(tool, capsys, monkeypatch):
    monkeypatch.setattr(tool, "last_output", lambda environ: "")
    save(tool, "galaxy-update")
    run(tool, capsys, "galaxy-update", environ=SYSTEMD_FAILED)
    assert tool.web.requests == [("POST", ADDRESS + "/fail", b"galaxy-update: FAILED (it ended with code 1)")]


def test_a_slash_on_the_end_of_the_saved_address_does_no_harm(tool, capsys):
    save(tool, "galaxy-update", ADDRESS + "/\n")
    run(tool, capsys, "galaxy-update", environ=SYSTEMD_FAILED)
    assert tool.web.requests[0][1] == ADDRESS + "/fail"


# -- when it cannot report: it says so, and never fails the job ------------------------


def test_without_an_address_file_it_only_says_it_is_not_reporting(tool, capsys):
    code, out = run(tool, capsys, "galaxy-update")
    assert code == 0 and tool.web.requests == []
    assert out.startswith("Not reporting how the galaxy-update job ended: %s does not exist."
                          % (tool.REPORT_DIR / "galaxy-update.url"))


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604])
def test_an_address_file_others_can_read_is_not_used(tool, capsys, mode):
    path = save(tool, "galaxy-update", mode=mode)
    code, out = run(tool, capsys, "galaxy-update")
    assert code == 0 and tool.web.requests == []
    assert "must be an ordinary file that belongs to root and that only root can read" in out
    assert "sudo chown root:root %s && sudo chmod 600 %s" % (path, path) in out


def test_an_address_file_that_is_not_roots_is_not_used(tool, capsys, monkeypatch):
    save(tool, "galaxy-update")
    monkeypatch.setattr(tool, "FILE_OWNER", os.getuid() + 1)
    code, out = run(tool, capsys, "galaxy-update")
    assert code == 0 and tool.web.requests == [] and "belongs to root" in out


def test_a_link_in_place_of_the_address_file_is_not_followed(tool, capsys, tmp_path):
    real = tmp_path / "elsewhere.url"
    real.write_text(ADDRESS + "\n", encoding="ascii")
    real.chmod(0o600)
    (tool.REPORT_DIR / "galaxy-update.url").symlink_to(real)
    code, out = run(tool, capsys, "galaxy-update")
    assert code == 0 and tool.web.requests == [] and "must be an ordinary file" in out


@pytest.mark.parametrize("text", [
    "http://hc-ping.com/0f3c4d5e-1111-2222-3333-444455556666\n",       # not https
    ADDRESS + "\n" + ADDRESS + "\n",                                    # two lines
    ADDRESS + "?x=1\n",                                                 # more than the address
    "hc-ping.com/0f3c4d5e-1111-2222-3333-444455556666\n",              # no https://
    "\n",
    ADDRESS + "/" + "a" * 600 + "\n",                                   # far too long
])
def test_an_address_file_holding_anything_but_one_https_address_is_not_used(tool, capsys, text):
    save(tool, "galaxy-update", text)
    code, out = run(tool, capsys, "galaxy-update")
    assert code == 0 and tool.web.requests == []
    assert "Could not report how the galaxy-update job ended" in out


def test_it_tries_three_times_and_then_gives_up_without_failing(tool, capsys):
    save(tool, "galaxy-update")
    tool.web.answers = [urllib.error.URLError("Name or service not known"), TimeoutError("timed out"), 503]
    code, out = run(tool, capsys, "galaxy-update")
    assert code == 0 and len(tool.web.requests) == 3
    assert out == ("Could not tell healthchecks.io that the galaxy-update job worked: it answered with "
                   "code 503. It notices by itself when no word arrives in time.\n")


def test_it_gets_through_on_a_later_try(tool, capsys):
    save(tool, "galaxy-update")
    tool.web.answers = [urllib.error.URLError("Temporary failure in name resolution"), 200]
    code, out = run(tool, capsys, "galaxy-update")
    assert code == 0 and len(tool.web.requests) == 2 and out.startswith("Told healthchecks.io")


def test_an_address_healthchecks_does_not_know_is_not_tried_again(tool, capsys):
    save(tool, "galaxy-update")
    tool.web.answers = [urllib.error.HTTPError(ADDRESS, 404, "Not Found", {}, io.BytesIO(b""))]
    code, out = run(tool, capsys, "galaxy-update")
    assert code == 0 and len(tool.web.requests) == 1
    assert "it answered with code 404: it does not know this address" in out


def test_an_error_that_names_the_address_does_not_get_it_printed(tool, capsys):
    save(tool, "galaxy-update")
    tool.web.answers = [urllib.error.URLError("cannot open " + ADDRESS)] * 3
    code, out = run(tool, capsys, "galaxy-update")   # run() checks the address is not in what was printed
    assert code == 0 and "it could not be reached (cannot open the address)" in out


def test_nothing_that_goes_wrong_here_fails_the_job(tool, capsys, monkeypatch):
    save(tool, "galaxy-update")

    def broken(address, body):
        raise RuntimeError("something unforeseen about " + ADDRESS)

    monkeypatch.setattr(tool, "send", broken)
    code, out = run(tool, capsys, "galaxy-update")
    assert code == 0 and out == "Could not report how the galaxy-update job ended: RuntimeError.\n"


def test_a_fetch_that_hangs_is_given_up_on_so_systemd_never_has_to_stop_this(tool, capsys, monkeypatch):
    import threading
    import time

    save(tool, "galaxy-update")
    release = threading.Event()
    monkeypatch.setattr(tool, "send", lambda address, body: (release.wait(30), (True, ""))[1])
    monkeypatch.setattr(tool, "SEND_LIMIT_SECONDS", 0.2)
    began = time.monotonic()
    code, out = run(tool, capsys, "galaxy-update")
    release.set()
    assert code == 0 and time.monotonic() - began < 5
    assert "Could not tell healthchecks.io that the galaxy-update job worked: it did not answer within" in out


def test_everything_here_fits_inside_the_time_systemd_allows():
    module = load()
    # journalctl twice at 15 s each, then the fetch; systemd allows 90 s.
    assert 2 * 15 + module.SEND_LIMIT_SECONDS < 90
    # And three tries with their pauses fit inside the fetch's own limit.
    assert module.ATTEMPTS * module.TIMEOUT_SECONDS + (module.ATTEMPTS - 1) * module.PAUSE_SECONDS \
        <= module.SEND_LIMIT_SECONDS


# -- by hand ---------------------------------------------------------------------------


def test_run_by_hand_it_explains_itself_and_sends_nothing(tool, capsys):
    save(tool, "galaxy-update")
    code, out = run(tool, capsys, "galaxy-update", environ={})
    assert code == 2 and tool.web.requests == []
    assert out == ("systemd runs this when the galaxy-update job ends. To try the saved address by hand: "
                   "sudo python3 report_in.py galaxy-update --test\n")


def test_a_test_by_hand_says_whether_the_saved_address_works(tool, capsys):
    path = save(tool, "galaxy-update")
    code, out = run(tool, capsys, "galaxy-update", "--test", environ={})
    assert code == 0 and tool.web.requests == [("POST", ADDRESS, b"galaxy-update: a test by hand")]
    assert out == "healthchecks.io took the test: the address in %s works.\n" % path

    tool.web.answers = [urllib.error.HTTPError(ADDRESS, 404, "Not Found", {}, io.BytesIO(b""))]
    code, out = run(tool, capsys, "galaxy-update", "--test", environ={})
    assert code == 0 and "healthchecks.io did not take the test: it answered with code 404" in out
    assert "Is the address in %s the whole of the one healthchecks.io shows" % path in out


@pytest.mark.parametrize("name", ["../etc/passwd", "Galaxy", "galaxy_update", "", "-x", "a" * 60])
def test_a_job_name_that_could_point_outside_the_folder_is_refused(tool, capsys, name):
    with pytest.raises(SystemExit) as stopped:
        tool.main([name], environ=SYSTEMD_WORKED)
    assert stopped.value.code == 2 and tool.web.requests == []
    capsys.readouterr()


# -- the last lines of a failed job ------------------------------------------------------


def test_the_last_lines_come_from_this_run_of_the_job_only(monkeypatch):
    module = load()
    commands = []

    class Done:
        returncode = 0
        stdout = ("line\n" * 3 + "FAILED: the update ended with code 1\n").encode()

    def fake_run(command, **options):
        commands.append(command)
        assert options.get("timeout"), "a log read with no time limit could hold the job's unit open"
        return Done()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.last_output(SYSTEMD_FAILED).endswith("FAILED: the update ended with code 1")
    assert commands == [
        ["journalctl", "--sync"],
        ["journalctl", "--no-pager", "--output=cat", "--lines=15",
         "_SYSTEMD_INVOCATION_ID=0123456789abcdef0123456789abcdef"]]

    # Nothing is asked of the log without a proper run id, and a log that
    # cannot be read gives nothing rather than an error.
    commands.clear()
    assert module.last_output({"INVOCATION_ID": "x; rm -rf /"}) == "" and commands == []
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert module.last_output(SYSTEMD_FAILED) == ""


def test_only_the_end_of_a_long_output_is_sent(monkeypatch):
    module = load()

    class Done:
        returncode = 0
        stdout = ("x" * 5000 + "THE END").encode()

    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: Done())
    found = module.last_output(SYSTEMD_FAILED)
    assert len(found) == module.TAIL_CHARACTERS and found.endswith("THE END")


# -- a real fetch, to a web server on this machine -------------------------------------------


def test_a_real_fetch_arrives_as_one_post_with_what_was_said(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    module = load()
    monkeypatch.setattr(module, "PAUSE_SECONDS", 0.0)
    got: list[tuple[str, str, bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802  (the name http.server calls)
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            got.append((self.command, self.path, body))
            code = 404 if self.path.startswith("/unknown") else 500 if self.path.startswith("/broken") else 200
            self.send_response(code)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = "http://127.0.0.1:%d" % server.server_address[1]
        assert module.send(base + "/a-check/fail", "state-refresh: FAILED (it ended with code 1)") == (True, "")
        assert got == [("POST", "/a-check/fail", b"state-refresh: FAILED (it ended with code 1)")]
        got.clear()
        # An address the server does not know: said once, not tried again.
        assert module.send(base + "/unknown", "x") == (
            False, "it answered with code 404: it does not know this address")
        assert len(got) == 1
        got.clear()
        # A server in trouble: tried three times.
        assert module.send(base + "/broken", "x") == (False, "it answered with code 500")
        assert len(got) == 3
    finally:
        server.shutdown()
        server.server_close()


# -- the jobs' unit files ------------------------------------------------------------------


@pytest.mark.parametrize("unit, job", [
    ("edfinder-galaxy-update.service", "galaxy-update"),
    ("edfinder-state-refresh.service", "state-refresh"),
])
def test_each_nightly_job_reports_in_when_it_ends_and_cannot_be_failed_by_it(unit, job):
    lines = (ROOT / "deploy" / unit).read_text(encoding="utf-8").splitlines()
    # The dash: systemd ignores whatever this command's own result is.
    assert "ExecStopPost=-/usr/bin/python3 /opt/edfinder/report_in.py %s" % job in lines
    assert sum(line.startswith("ExecStopPost=") for line in lines) == 1
    # The job's name is the unit's, so each has its own address file.
    assert unit == "edfinder-%s.service" % job
    assert load().JOB.fullmatch(job)
