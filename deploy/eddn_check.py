#!/usr/bin/env python3
"""Look at the EDDN listener, and start it afresh if it has stopped working.

    python3 eddn_check.py          what the timer runs: look, and restart it if unwell
    python3 eddn_check.py --look   only look and say; change nothing

The listener (deploy/pi/eddn.yml) keeps station sightings from the live feed.
They can only be gathered as they happen: whatever it misses is gone for
good. Docker starts the listener again when it stops, and it stops itself
after five failed writes in a row. But Docker does nothing about a container
that is still running and no longer working, and nobody was told about one.
The listener has a health check of its own, which Docker runs every 30
seconds: it is "unhealthy" while a write has failed and not yet been made
good, or when no message has come from the feed for five minutes.

So every half hour (edfinder-eddn-check.timer) this asks Docker how the
listener is:

    running and healthy     all is well
    running and just started   all is well; its first health check is not in yet
    running and unhealthy   it is started afresh, and this run counts as failed
    not running, or absent  this run counts as failed; nothing is started, since
                            it may have been stopped on purpose

A failed run is reported like any nightly job's (deploy/report_in.py), so an
email follows. The feed is silent for a while during the game's weekly
maintenance; a failed run then is expected, and the next run that finds the
listener healthy clears it. If the listener is stopped on purpose, stop the
timer too:

    sudo systemctl disable --now edfinder-eddn-check.timer

Being the one look that runs all day, it also asks systemd whether any of
ED-Finder's timers has stopped. A timer that cannot start its job ("Failed to
queue unit startup job") is marked failed, and from then on it never goes off:
the job does not run, so nothing fails, so nobody is told. On the Pi the
weekly full backup's timer sat like that from 22 to 25 November 2026. A
stopped timer fails this run, which names it and says how to restart it;
nothing is restarted from here, since restarting some timers starts a long
job at once. A timer that was switched off on purpose (disabled) is not
"failed" and is left alone.

Standard library only; needs Python 3.8 or newer and to run as root (Docker).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Callable, List, Optional, Sequence, Tuple

CONTAINER = "edfinder-eddn"
# How long Docker waits for the listener to write what it holds before
# stopping it: the same as stop_grace_period in deploy/pi/eddn.yml.
STOP_SECONDS = 30
COMMAND_SECONDS = 120

# What Docker is asked: whether the container runs, what its health check
# says, and how many times in a row that check has failed.
FORMAT = ("{{.State.Status}} "
          "{{if .State.Health}}{{.State.Health.Status}} {{.State.Health.FailingStreak}}"
          "{{else}}none 0{{end}}")

# ED-Finder's own timers, as systemd is asked for them.
TIMERS = "edfinder-*.timer"

Docker = Callable[[Sequence[str]], Tuple[int, str]]
Systemctl = Callable[[Sequence[str]], Tuple[int, str]]


def docker(arguments: Sequence[str]) -> Tuple[int, str]:
    """Run one Docker command. Its exit code, and what it printed (both streams)."""
    try:
        result = subprocess.run(["docker", *arguments], capture_output=True, text=True,
                                timeout=COMMAND_SECONDS)
    except FileNotFoundError:
        return 127, "docker is not installed"
    except subprocess.TimeoutExpired:
        return 124, "docker did not answer within %d seconds" % COMMAND_SECONDS
    except PermissionError:
        return 126, "docker could not be run; this needs sudo"
    return result.returncode, (result.stdout + result.stderr).strip()


def systemctl(arguments: Sequence[str]) -> Tuple[int, str]:
    """Run one systemctl command. Its exit code, and what it printed (both streams)."""
    try:
        result = subprocess.run(["systemctl", *arguments], capture_output=True, text=True,
                                timeout=COMMAND_SECONDS)
    except FileNotFoundError:
        return 127, "systemctl is not installed"
    except subprocess.TimeoutExpired:
        return 124, "systemctl did not answer within %d seconds" % COMMAND_SECONDS
    except PermissionError:
        return 126, "systemctl could not be run"
    return result.returncode, (result.stdout + result.stderr).strip()


def stopped_timers(ask: Systemctl) -> Tuple[bool, List[str]]:
    """Whether none of ED-Finder's timers has stopped, and the lines to print.

    systemd lists the ones it has marked failed, one a line: the timer's name,
    then "loaded failed failed" and its description. Some versions put a mark
    in front of the name.
    """
    code, printed = ask(["list-units", "--type=timer", "--state=failed", "--all",
                         "--plain", "--no-legend", "--no-pager", TIMERS])
    if code != 0:
        return False, ["Could not ask systemd about ED-Finder's timers: %s" % (
            printed.splitlines()[-1] if printed else "systemctl gave no reason")]
    names = []
    for line in printed.splitlines():
        name = next((word for word in line.split() if word.endswith(".timer")), None)
        if name and "failed" in line.split() and name not in names:
            names.append(name)
    lines = []
    for name in names:
        lines += ["The timer %s has stopped: systemd could not start its job and has given up "
                  "on the timer. The job will not run again until the timer is restarted." % name,
                  "To see why: systemctl status %s --no-pager" % name,
                  "To restart it (a job whose time was missed then starts at once): "
                  "sudo systemctl restart %s" % name]
    return not names, lines


def look(ask: Docker) -> Tuple[str, str, int]:
    """How the listener is: (whether it runs, its health, failed checks in a row).

    Whether it runs is Docker's own word: "running", "exited", "restarting"
    and so on, or "absent" when there is no such container, or "unknown" when
    Docker could not be asked.
    """
    code, printed = ask(["inspect", "--type", "container", "--format", FORMAT, CONTAINER])
    if code != 0:
        if "no such" in printed.lower():
            return "absent", "none", 0
        return "unknown", printed.splitlines()[-1] if printed else "docker gave no reason", 0
    parts = printed.split()
    if len(parts) != 3 or not parts[2].isdigit():
        return "unknown", "docker's answer was not understood: %r" % printed[:80], 0
    return parts[0], parts[1], int(parts[2])


def check(ask: Docker, restart: bool) -> Tuple[bool, List[str]]:
    """Look, restart the listener if it is running and unhealthy, and say what was found.

    Returns whether all is well, and the lines to print.
    """
    running, health, failed = look(ask)
    if running == "unknown":
        return False, ["Could not ask Docker about the listener: %s" % health]
    if running == "absent":
        return False, [
            "The listener's container (%s) is not on this machine." % CONTAINER,
            "To start it: sudo docker compose -f /opt/edfinder/eddn.yml up -d"]
    if running != "running":
        return False, [
            "The listener is not running (Docker says: %s)." % running,
            "It was not started from here, in case it was stopped on purpose.",
            "To start it: sudo docker compose -f /opt/edfinder/eddn.yml up -d"]
    if health in ("healthy", "starting"):
        return True, ["The listener is running and %s." % (
            "healthy" if health == "healthy" else "has just started")]
    if health == "none":
        return False, ["The listener is running, but its image has no health check, "
                       "so nothing can say whether it is working."]
    lines = ["The listener is running but not working: its own health check has failed "
             "%d time%s in a row." % (failed, "" if failed == 1 else "s"),
             "That means a write to the database failed and has not been made good, or "
             "no message has come from the feed for five minutes."]
    if not restart:
        return False, lines + ["Nothing was changed (--look)."]
    code, printed = ask(["restart", "-t", str(STOP_SECONDS), CONTAINER])
    if code == 0:
        lines.append("It has been started afresh. The next check, in half an hour, "
                     "says whether that helped.")
    else:
        lines.append("Starting it afresh failed: %s" % (
            printed.splitlines()[-1] if printed else "docker gave no reason"))
    return False, lines


def main(argv: Optional[Sequence[str]] = None, ask: Docker = docker,
         timers: Systemctl = systemctl) -> int:
    parser = argparse.ArgumentParser(
        description="Look at the EDDN listener, and start it afresh if it has stopped working. "
                    "Also say whether any of ED-Finder's timers has stopped.")
    parser.add_argument("--look", action="store_true",
                        help="only look and say; change nothing")
    arguments = parser.parse_args(argv)
    well, lines = check(ask, restart=not arguments.look)
    going, more = stopped_timers(timers)
    for line in lines + more:
        print(line, flush=True)
    return 0 if well and going else 1


if __name__ == "__main__":
    sys.exit(main())
