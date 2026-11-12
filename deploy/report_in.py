#!/usr/bin/env python3
"""Tell healthchecks.io how a nightly job ended, so that a failure sends an email.

    python3 report_in.py galaxy-update          systemd runs this when the job ends
    python3 report_in.py galaxy-update --test   by hand: send one "it worked", to
                                                see that the saved address is right

The nightly jobs run with nobody watching. A job that fails, or does not run at
all, would otherwise tell nobody. So each job has a "check" on healthchecks.io,
and each check has its own web address. When a job ends, this fetches that
address: as it is when the job worked, and with /fail on the end when it did
not. healthchecks.io emails the owner when it is told of a failure. It also
emails when it has heard nothing for longer than the check allows: the machine
is off, has no internet, or the job is stuck.

systemd runs this after the job, whichever way the job ended (the
ExecStopPost= line in the job's .service file). It says how the job ended in
SERVICE_RESULT: "success", or the kind of failure. When a job failed, the last
lines it printed are sent too, so the reason is kept with the failure.

A check's address is a secret: anyone who has it can report "all is well". It
is kept in a file that only root can read, one file per job, holding that one
line, and it is never printed:

    /etc/edfinder/report/galaxy-update.url
    /etc/edfinder/report/state-refresh.url

Until a job's file exists this only says that it is not reporting. Nothing here
can make the job itself fail: whatever goes wrong, it says so and ends with
code 0.

Standard library only; needs Python 3.8 or newer.
"""
from __future__ import annotations

import argparse
import http.client
import os
import re
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Mapping, Optional, Tuple

REPORT_DIR = Path("/etc/edfinder/report")
# The address files must belong to this user (root) and be closed to others.
FILE_OWNER = 0
JOB = re.compile(r"[a-z][a-z0-9-]{0,40}")
ADDRESS = re.compile(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?/[A-Za-z0-9._~/-]{1,200}")
INVOCATION = re.compile(r"[0-9a-f]{32}")
# systemd waits 90 seconds for this to finish, then stops it and marks the
# job's unit as failed. So everything here has a time limit: reading the log
# (twice 15 seconds at most), then the fetch (three tries, and 45 seconds at
# the very most, whatever the network does).
ATTEMPTS = 3
PAUSE_SECONDS = 5.0
TIMEOUT_SECONDS = 10.0
SEND_LIMIT_SECONDS = 45.0
# How much of a failed job's last output is sent along.
TAIL_LINES = 15
TAIL_CHARACTERS = 2000


class NotSetUp(Exception):
    """This job has no address file: reporting has not been set up for it."""


class Unusable(Exception):
    """The address file is there but cannot be used; the message says why."""


def say(message: str) -> None:
    print(message, flush=True)


def address_file(job: str) -> Path:
    return REPORT_DIR / (job + ".url")


def read_address(job: str) -> str:
    """The check's address for this job. It is a secret: never print it."""
    path = address_file(job)
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise NotSetUp("%s does not exist" % path) from None
    except PermissionError:
        raise Unusable("%s cannot be read by this user. Run this with sudo" % path) from None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != FILE_OWNER or info.st_mode & 0o077:
        raise Unusable(
            "%s must be an ordinary file that belongs to root and that only root can read. "
            "To put that right: sudo chown root:root %s && sudo chmod 600 %s" % (path, path, path))
    if info.st_size > 512:
        raise Unusable("%s is too long to be one web address" % path)
    try:
        text = path.read_text(encoding="ascii").strip()
    except PermissionError:
        raise Unusable("%s cannot be read by this user. Run this with sudo" % path) from None
    except (OSError, UnicodeDecodeError):
        raise Unusable("%s could not be read as plain text" % path) from None
    if not ADDRESS.fullmatch(text):
        raise Unusable("%s must hold one line: the check's https address, and nothing else" % path)
    return text.rstrip("/")


def how_it_ended(environ: Mapping[str, str]) -> Tuple[bool, str]:
    """(whether the job worked, a few words on how it ended), from what systemd
    says: SERVICE_RESULT, and EXIT_STATUS (the exit code, or a signal's name)."""
    result = environ.get("SERVICE_RESULT", "")
    if result == "success":
        return True, "worked"
    status = environ.get("EXIT_STATUS", "")
    if result == "exit-code":
        why = "it ended with code %s" % (status or "?")
    elif result == "timeout":
        why = "it ran for longer than it is allowed and was stopped"
    elif result == "oom-kill":
        why = "the machine ran out of memory"
    elif result == "signal":
        why = "it was stopped from outside, signal %s" % (status or "?")
    else:
        why = "systemd says: %s" % (result or "nothing")
    return False, "FAILED (%s)" % why


def last_output(environ: Mapping[str, str]) -> str:
    """The last lines this run of the job printed, from the system's log.
    Empty if they cannot be had: the report is sent without them."""
    invocation = environ.get("INVOCATION_ID", "")
    if not INVOCATION.fullmatch(invocation):
        return ""
    try:
        # Have the log take in anything the job has only just printed.
        subprocess.run(["journalctl", "--sync"], stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
        found = subprocess.run(
            ["journalctl", "--no-pager", "--output=cat", "--lines=%d" % TAIL_LINES,
             "_SYSTEMD_INVOCATION_ID=" + invocation],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    if found.returncode != 0:
        return ""
    return found.stdout.decode("utf-8", "replace").strip()[-TAIL_CHARACTERS:]


def send(address: str, body: str) -> Tuple[bool, str]:
    """Fetch the address, with the body as what is said. (True, "") or
    (False, why not). The reason never includes the address."""
    request = urllib.request.Request(address, data=body.encode("utf-8"), method="POST",
                                     headers={"User-Agent": "edfinder-report-in",
                                              "Content-Type": "text/plain; charset=utf-8"})
    reason = "no answer"
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as answer:
                if 200 <= answer.status < 300:
                    return True, ""
                reason = "it answered with code %d" % answer.status
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()  # it holds the answer open
            reason = "it answered with code %d" % code
            if code == 404:
                return False, reason + ": it does not know this address"
            if 400 <= code < 500 and code != 429:
                return False, reason
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as error:
            detail = str(getattr(error, "reason", "") or error)
            reason = "it could not be reached (%s)" % detail.replace(address, "the address")
        if attempt < ATTEMPTS:
            time.sleep(PAUSE_SECONDS)
    return False, reason


def send_in_time(address: str, body: str) -> Tuple[bool, str]:
    """``send``, given up on after SEND_LIMIT_SECONDS. A time limit on the
    connection alone does not cover everything: looking up the name can hang
    for longer when the network is half-down."""
    outcome: List[object] = []

    def work() -> None:
        try:
            outcome.append(send(address, body))
        except BaseException as error:  # noqa: BLE001  (handed to the caller below)
            outcome.append(error)

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(SEND_LIMIT_SECONDS)
    if not outcome:
        return False, "it did not answer within %d seconds" % SEND_LIMIT_SECONDS
    if isinstance(outcome[0], BaseException):
        raise outcome[0]
    return outcome[0]  # type: ignore[return-value]


def report(job: str, environ: Mapping[str, str], test: bool) -> int:
    if not test and "SERVICE_RESULT" not in environ:
        say("systemd runs this when the %s job ends. To try the saved address by hand: "
            "sudo python3 %s %s --test" % (job, Path(__file__).name, job))
        return 2
    try:
        address = read_address(job)
    except NotSetUp as error:
        say("Not reporting how the %s job ended: %s. To be told when this job fails, save its "
            "healthchecks.io address in that file." % (job, error))
        return 0
    except Unusable as error:
        say("Could not report how the %s job ended: %s." % (job, error))
        return 0
    if test:
        sent, why = send_in_time(address, "%s: a test by hand" % job)
        if sent:
            say("healthchecks.io took the test: the address in %s works." % address_file(job))
        else:
            say("healthchecks.io did not take the test: %s. Is the address in %s the whole of the "
                "one healthchecks.io shows for this check?" % (why, address_file(job)))
        return 0
    worked, words = how_it_ended(environ)
    body = "%s: %s" % (job, words)
    if not worked:
        output = last_output(environ)
        if output:
            body += "\n\nThe last lines it printed:\n" + output
    sent, why = send_in_time(address if worked else address + "/fail", body)
    if sent:
        say("Told healthchecks.io that the %s job %s." % (job, words))
    else:
        say("Could not tell healthchecks.io that the %s job %s: %s. It notices by itself when "
            "no word arrives in time." % (job, words, why))
    return 0


def job_name(text: str) -> str:
    if not JOB.fullmatch(text):
        raise argparse.ArgumentTypeError("a job's name is small letters, digits and dashes, like galaxy-update")
    return text


def main(argv: Optional[List[str]] = None, environ: Optional[Mapping[str, str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Tell healthchecks.io how a nightly job ended. systemd runs this when the job ends.")
    parser.add_argument("job", type=job_name, help="the job's name, for example galaxy-update")
    parser.add_argument("--test", action="store_true",
                        help="send one 'it worked' by hand, to see that the saved address is right")
    args = parser.parse_args(argv)
    try:
        return report(args.job, os.environ if environ is None else environ, args.test)
    except Exception as error:  # noqa: BLE001  (nothing here may fail the job it reports on)
        say("Could not report how the %s job ended: %s." % (args.job, type(error).__name__))
        return 0


if __name__ == "__main__":
    sys.exit(main())
