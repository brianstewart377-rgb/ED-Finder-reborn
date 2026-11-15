"""The check on the EDDN listener (deploy/eddn_check.py).

The listener's sightings can only be gathered as they happen. Docker starts
it again when it stops, but does nothing about a container that is running
and no longer working, and nobody was told about one. The check asks Docker
how the listener is every half hour, starts an unwell one afresh, and fails
its run (which is reported, so an email follows) whenever it is not working.

Most of this is checked with Docker's answers played back. The last test
asks the real Docker about a real container that is unwell, because what
matters most here is that Docker's answer is read the way Docker gives it.
CI sets REQUIRE_DOCKER_TESTS=1, so that test cannot be skipped there.
"""
from __future__ import annotations

import importlib.util
import time
import uuid
from pathlib import Path

import pytest

from tests.test_deploy_docker import _REASON as NO_DOCKER
from tests.test_deploy_docker import base_image
from tests.test_deploy_docker import docker as real_docker

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("eddn_check", ROOT / "deploy" / "eddn_check.py")
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)


class PlayedBack:
    """Docker, answering "how is the listener?" with one fixed answer."""

    def __init__(self, answer: tuple[int, str], restart: tuple[int, str] = (0, "edfinder-eddn")) -> None:
        self.answer, self.restart = answer, restart
        self.asked: list[list[str]] = []

    def __call__(self, arguments):
        self.asked.append(list(arguments))
        return self.restart if arguments[0] == "restart" else self.answer

    @property
    def restarts(self) -> list[list[str]]:
        return [arguments for arguments in self.asked if arguments[0] == "restart"]


def run(docker: PlayedBack, *argv: str, capsys) -> tuple[int, str]:
    code = tool.main(list(argv), ask=docker)
    return code, capsys.readouterr().out


def test_a_healthy_listener_is_left_alone(capsys):
    docker = PlayedBack((0, "running healthy 0"))
    code, said = run(docker, capsys=capsys)
    assert code == 0 and said == "The listener is running and healthy.\n"
    assert docker.restarts == []
    # One question, about the listener's container and nothing else.
    assert docker.asked == [["inspect", "--type", "container", "--format", tool.FORMAT, "edfinder-eddn"]]


def test_a_listener_that_has_just_started_is_given_time(capsys):
    docker = PlayedBack((0, "running starting 0"))
    code, said = run(docker, capsys=capsys)
    assert code == 0 and "has just started" in said
    assert docker.restarts == []


def test_a_listener_that_runs_but_is_unwell_is_started_afresh_and_the_run_fails(capsys):
    docker = PlayedBack((0, "running unhealthy 7"))
    code, said = run(docker, capsys=capsys)
    # Failed, so that it is reported and an email follows.
    assert code == 1
    assert "its own health check has failed 7 times in a row" in said
    assert "It has been started afresh." in said
    # Thirty seconds to write what it holds: the same as in deploy/pi/eddn.yml.
    assert docker.restarts == [["restart", "-t", "30", "edfinder-eddn"]]
    assert "stop_grace_period: 30s" in (ROOT / "deploy" / "pi" / "eddn.yml").read_text()


def test_looking_only_changes_nothing(capsys):
    docker = PlayedBack((0, "running unhealthy 1"))
    code, said = run(docker, "--look", capsys=capsys)
    assert code == 1
    assert "has failed 1 time in a row" in said and "Nothing was changed (--look)." in said
    assert docker.restarts == []


@pytest.mark.parametrize("status", ["exited", "restarting", "paused", "created", "dead"])
def test_a_listener_that_is_not_running_fails_the_run_and_is_not_started(status, capsys):
    """It may have been stopped on purpose, and Docker's own restart rule is
    already at work on one that crashed."""
    docker = PlayedBack((0, "%s none 0" % status))
    code, said = run(docker, capsys=capsys)
    assert code == 1
    assert "The listener is not running (Docker says: %s)." % status in said
    assert "sudo docker compose -f /opt/edfinder/eddn.yml up -d" in said
    assert docker.restarts == []


@pytest.mark.parametrize("printed", [
    "Error response from daemon: No such container: edfinder-eddn",
    "Error: No such object: edfinder-eddn",
])
def test_a_machine_without_the_listener_says_how_to_start_it(printed, capsys):
    docker = PlayedBack((1, printed))
    code, said = run(docker, capsys=capsys)
    assert code == 1
    assert "The listener's container (edfinder-eddn) is not on this machine." in said
    assert docker.restarts == []


@pytest.mark.parametrize("answer, reason", [
    ((126, "docker could not be run; this needs sudo"), "this needs sudo"),
    ((1, "Cannot connect to the Docker daemon at unix:///var/run/docker.sock."), "Cannot connect"),
    ((0, "something else entirely"), "was not understood"),
    ((0, ""), "was not understood"),
])
def test_when_docker_cannot_say_the_run_fails_with_the_reason(answer, reason, capsys):
    docker = PlayedBack(answer)
    code, said = run(docker, capsys=capsys)
    assert code == 1
    assert "Could not ask Docker about the listener" in said and reason in said
    assert docker.restarts == []


def test_a_listener_with_no_health_check_is_not_called_well(capsys):
    docker = PlayedBack((0, "running none 0"))
    code, said = run(docker, capsys=capsys)
    assert code == 1 and "has no health check" in said
    assert docker.restarts == []


def test_a_restart_that_fails_is_said(capsys):
    docker = PlayedBack((0, "running unhealthy 3"),
                        restart=(1, "Error response from daemon: cannot restart container"))
    code, said = run(docker, capsys=capsys)
    assert code == 1
    assert "Starting it afresh failed: Error response from daemon: cannot restart container" in said


def test_the_timer_runs_it_every_half_hour_and_the_listener_keeps_its_health_check():
    timer = (ROOT / "deploy" / "edfinder-eddn-check.timer").read_text()
    assert "OnCalendar=*-*-* *:07,37:00 UTC" in timer.splitlines()
    service = (ROOT / "deploy" / "edfinder-eddn-check.service").read_text().splitlines()
    assert "ExecStart=/usr/bin/python3 /opt/edfinder/eddn_check.py" in service
    # The check reads the health check the listener's image carries.
    assert "HEALTHCHECK" in (ROOT / "apps" / "eddn_v3" / "Dockerfile").read_text()


@pytest.mark.skipif(bool(NO_DOCKER), reason=NO_DOCKER or "available")
def test_the_real_docker_is_read_rightly_about_a_real_unwell_container(monkeypatch, capsys):
    name = "edft-eddn-check-" + uuid.uuid4().hex[:8]
    monkeypatch.setattr(tool, "CONTAINER", name)
    monkeypatch.setattr(tool, "STOP_SECONDS", 2)

    def wait_for(health: str) -> tuple[str, str, int]:
        seen = ("", "", 0)
        for _ in range(120):
            seen = tool.look(tool.docker)
            if seen[1] == health:
                return seen
            time.sleep(0.25)
        raise AssertionError("the container never became %s; last seen: %r" % (health, seen))

    assert tool.look(tool.docker) == ("absent", "none", 0)
    assert tool.main([], ask=tool.docker) == 1
    assert "is not on this machine" in capsys.readouterr().out
    try:
        # A container with a health check that fails until a file is made in
        # it: running, and unwell, as a stuck listener would be.
        real_docker("run", "-d", "--name", name, "--health-cmd", "test -f /tmp/well",
                    "--health-interval", "1s", "--health-retries", "1",
                    "--health-start-period", "0s", base_image())
        running, health, failed = wait_for("unhealthy")
        assert running == "running" and failed >= 1
        started = real_docker("inspect", "--format", "{{.State.StartedAt}}", name)

        assert tool.main(["--look"], ask=tool.docker) == 1
        assert "Nothing was changed (--look)." in capsys.readouterr().out
        assert real_docker("inspect", "--format", "{{.State.StartedAt}}", name) == started

        assert tool.main([], ask=tool.docker) == 1
        assert "It has been started afresh." in capsys.readouterr().out
        assert real_docker("inspect", "--format", "{{.State.StartedAt}}", name) != started

        # Made well, it is found well and left alone.
        real_docker("exec", name, "touch", "/tmp/well")
        wait_for("healthy")
        started = real_docker("inspect", "--format", "{{.State.StartedAt}}", name)
        assert tool.main([], ask=tool.docker) == 0
        assert capsys.readouterr().out == "The listener is running and healthy.\n"
        assert real_docker("inspect", "--format", "{{.State.StartedAt}}", name) == started

        # Stopped by hand: said, and not started.
        real_docker("stop", "-t", "1", name)
        assert tool.main([], ask=tool.docker) == 1
        assert "The listener is not running (Docker says: exited)." in capsys.readouterr().out
        assert tool.look(tool.docker)[0] == "exited"
    finally:
        real_docker("rm", "-f", name, check=False)
