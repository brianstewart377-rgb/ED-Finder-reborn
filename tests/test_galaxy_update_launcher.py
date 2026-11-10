"""The nightly galaxy update's launcher (deploy/galaxy_update.py).

A small local web server stands in for Spansh, and a stand-in ``docker``
program records what it is asked to do. What the update does inside its
container is tested against PostgreSQL in tests/test_galaxy_update.py.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from tests.test_state_refresh import MONDAY, TUESDAY, Spansh

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "deploy" / "galaxy_update.py"

_spec = importlib.util.spec_from_file_location("galaxy_update_launcher", TOOL)
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

FILE = "galaxy_1day.json.gz"
IMAGE = "ghcr.io/example/api:git-" + "a" * 40

# A stand-in for docker: writes each call to a log and answers from a script
# of canned replies, so the launcher's exact commands can be checked.
FAKE_DOCKER = r'''#!%(python)s
import json, os, sys
state = json.load(open(os.environ["FAKE_DOCKER_STATE"]))
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\n")
args = sys.argv[1:]
if args[0] == "inspect":
    name = args[-1]
    if name in state["containers"]:
        print(state["containers"][name])
        sys.exit(0)
    sys.exit(1)
if args[0] == "run":
    route = "app" if "--env-file" in args else "local"
    print("%%s (%%s) by the %%s route" %% (args[args.index("-m") + 1], args[args.index("-m") + 2], route))
    sys.exit(state["run_exit"][route])
if args[0] == "exec":
    statement = args[-1]
    for needle, reply in state["psql"].items():
        if needle in statement:
            if reply is None:
                sys.stderr.write("ERROR: relation does not exist\n")
                sys.exit(1)
            print(reply)
            sys.exit(0)
    sys.exit(0)
sys.exit(64)
'''


class Docker:
    def __init__(self, directory: Path, monkeypatch) -> None:
        self.state_file = directory / "state.json"
        self.log_file = directory / "log.jsonl"
        self.state = {
            "containers": {"edfinder-api": IMAGE},
            "run_exit": {"local": 0, "app": 0},
            "psql": {},
        }
        binary = directory / "bin" / "docker"
        binary.parent.mkdir()
        binary.write_text(FAKE_DOCKER % {"python": sys.executable}, encoding="utf-8")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv("PATH", "%s%s%s" % (binary.parent, os.pathsep, os.environ["PATH"]))
        monkeypatch.setenv("FAKE_DOCKER_STATE", str(self.state_file))
        monkeypatch.setenv("FAKE_DOCKER_LOG", str(self.log_file))
        self.save()

    def save(self) -> None:
        self.state_file.write_text(json.dumps(self.state), encoding="utf-8")

    def calls(self, verb: str) -> list[list[str]]:
        if not self.log_file.exists():
            return []
        calls = [json.loads(line) for line in self.log_file.read_text().splitlines()]
        return [call for call in calls if call[0] == verb]


@pytest.fixture
def spansh(monkeypatch):
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    site = Spansh()
    yield site
    site.close()


@pytest.fixture
def docker(tmp_path, monkeypatch):
    directory = tmp_path / "docker"
    directory.mkdir()
    return Docker(directory, monkeypatch)


SECRET = "sekret-not-a-real-password"


def settings(spansh: Spansh, directory: Path, **extra: str):
    # Stands in for the API's settings file. The launcher must never open it.
    env_file = directory.parent / "api.env"
    env_file.write_text("DATABASE_URL=postgresql://app:%s@db:5432/app\n" % SECRET, encoding="utf-8")
    environ = {
        "EDFINDER_GALAXY_DIR": str(directory),
        "EDFINDER_GALAXY_URL": "%s/%s" % (spansh.origin, FILE),
        "EDFINDER_API_ENV_FILE": str(env_file),
    }
    environ.update(extra)
    return tool.Settings(environ=environ, conf={})


# -- settings ---------------------------------------------------------------


def test_defaults_point_at_spansh_and_the_production_database():
    chosen = tool.Settings(environ={}, conf={})
    assert chosen.url == "https://downloads.spansh.co.uk/galaxy_1day.json.gz"
    assert chosen.target == Path("/var/lib/edfinder-galaxy/galaxy_1day.json.gz")
    assert chosen.api_container == "edfinder-api"
    assert chosen.psql == [
        "docker", "exec", "-i", "edfinder-v3-phase4c-full-20260827_r5-postgres", "psql", "-X",
        "-U", "edfinder_v3", "-d", "edfinder_v3_phase4c_full_20260827_r5"]


def test_environment_wins_over_the_conf_file_which_wins_over_defaults():
    chosen = tool.Settings(
        environ={"EDFINDER_DB_NAME": "from_environment"},
        conf={"EDFINDER_DB_NAME": "from_conf", "EDFINDER_DB_USER": "conf_user"})
    assert (chosen.db_name, chosen.db_user) == ("from_environment", "conf_user")
    assert chosen.db_container == "edfinder-v3-phase4c-full-20260827_r5-postgres"


@pytest.mark.parametrize("key, value, message", [
    ("EDFINDER_GALAXY_URL", "galaxy_1day.json.gz", "is not a file address"),
    ("EDFINDER_DB_CONTAINER", "pg; rm -rf /", "is not a plain name"),
    ("EDFINDER_DB_USER", "", "is not a plain name"),
    ("EDFINDER_DB_NAME", "name with spaces", "is not a plain name"),
    ("EDFINDER_PROJECT", "--privileged", "is not a plain name"),
    ("EDFINDER_NETWORK", "host --privileged", "is not a plain name"),
    ("EDFINDER_GALAXY_CONNECT", "magic", "must be auto, local or app"),
])
def test_bad_settings_are_refused(key, value, message):
    with pytest.raises(tool.UpdateError, match=message):
        tool.Settings(environ={key: value}, conf={})


# -- fetching and running ---------------------------------------------------


def test_run_downloads_the_file_and_runs_the_update_from_the_live_image(spansh, docker, tmp_path, capsys):
    spansh.publish(FILE, b"night one" * 100, MONDAY)
    chosen = settings(spansh, tmp_path / "galaxy")

    assert tool.cmd_apply(chosen, "run", None) == 0

    assert chosen.target.read_bytes() == b"night one" * 100
    (run,) = docker.calls("run")
    assert run == [
        "run", "--rm", "--name", "edfinder-galaxy-update",
        "--network", "container:edfinder-v3-phase4c-full-20260827_r5-postgres",
        "--env", "PGHOST=127.0.0.1", "--env", "PGPORT=5432",
        "--env", "PGUSER=edfinder_v3", "--env", "PGDATABASE=edfinder_v3_phase4c_full_20260827_r5",
        "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTHONUNBUFFERED=1",
        "--volume", "%s:/data:ro" % chosen.directory,
        "--security-opt", "no-new-privileges:true", "--pids-limit", "256",
        IMAGE, "python", "-m", "spansh_delta.apply", "apply", "/data/" + FILE,
        "--made-at", "2026-11-09T02:10:00+00:00",
    ]
    # Nothing secret is passed: no password setting of any kind.
    assert not [part for part in run if "PASSWORD" in part.upper() or "postgresql://" in part]
    # The container can read the file whoever it runs as.
    assert stat.S_IMODE(chosen.target.stat().st_mode) == 0o644
    assert stat.S_IMODE(chosen.directory.stat().st_mode) == 0o755
    out = capsys.readouterr().out
    assert "Downloading galaxy_1day.json.gz" in out and "Running the update from " + IMAGE in out


def test_check_scores_uses_the_file_already_on_disk_and_is_read_only(spansh, docker, tmp_path, capsys):
    spansh.publish(FILE, b"night one", MONDAY)
    chosen = settings(spansh, tmp_path / "galaxy")
    assert tool.cmd_apply(chosen, "run", None) == 0
    # Spansh publishes the next file; the check must still use the one that was applied.
    spansh.publish(FILE, b"night two!", TUESDAY)
    capsys.readouterr()

    assert tool.cmd_apply(chosen, "check-scores", None) == 0

    assert spansh.downloads() == [FILE] and chosen.target.read_bytes() == b"night one"
    check = docker.calls("run")[-1]
    assert check[check.index("-m"):] == ["-m", "spansh_delta.scores", "check", "/data/" + FILE]
    assert "container:edfinder-v3-phase4c-full-20260827_r5-postgres" in check
    out = capsys.readouterr().out
    assert "Running the check from " + IMAGE in out and "Asking Spansh" not in out
    # It does not count as applying anything.
    assert tool.fetch.read_record(chosen.record)["applied"]["modified"] == MONDAY


def test_check_scores_fetches_the_file_when_there_is_none_and_passes_a_limit(spansh, docker, tmp_path):
    spansh.publish(FILE, b"night one", MONDAY)
    chosen = settings(spansh, tmp_path / "galaxy")
    assert tool.cmd_apply(chosen, "check-scores", 500) == 0
    assert spansh.downloads() == [FILE] and not chosen.record.exists()
    check = docker.calls("run")[-1]
    assert check[check.index("-m"):] == ["-m", "spansh_delta.scores", "check", "/data/" + FILE,
                                         "--limit", "500"]


def test_a_failed_score_check_says_so(spansh, docker, tmp_path, capsys):
    spansh.publish(FILE, b"night one", MONDAY)
    chosen = settings(spansh, tmp_path / "galaxy")
    assert tool.cmd_apply(chosen, "run", None) == 0
    docker.state["run_exit"]["local"] = 1
    docker.save()
    assert tool.cmd_apply(chosen, "check-scores", None) == 1
    assert "FAILED: the check ended with code 1" in capsys.readouterr().out


def test_a_second_run_of_the_same_file_does_nothing_and_a_new_file_runs_again(
        spansh, docker, tmp_path, capsys):
    spansh.publish(FILE, b"night one", MONDAY)
    chosen = settings(spansh, tmp_path / "galaxy")
    assert tool.cmd_apply(chosen, "run", None) == 0
    capsys.readouterr()

    assert tool.cmd_apply(chosen, "run", None) == 0
    assert "nothing new since the last update" in capsys.readouterr().out
    assert len(docker.calls("run")) == 1 and spansh.downloads() == [FILE]

    spansh.publish(FILE, b"night two!", TUESDAY)
    assert tool.cmd_apply(chosen, "run", None) == 0
    assert len(docker.calls("run")) == 2 and spansh.downloads() == [FILE, FILE]
    assert docker.calls("run")[-1][-1] == "2026-11-10T02:10:00+00:00"


def test_check_is_read_only_runs_every_time_and_does_not_count_as_applied(spansh, docker, tmp_path):
    spansh.publish(FILE, b"night one", MONDAY)
    chosen = settings(spansh, tmp_path / "galaxy")
    assert tool.cmd_apply(chosen, "check", None) == 0
    assert tool.cmd_apply(chosen, "check", None) == 0
    checks = docker.calls("run")
    assert [call[call.index("spansh_delta.apply") + 1] for call in checks] == ["check", "check"]
    assert spansh.downloads() == [FILE]  # the file is fetched once
    assert not chosen.record.exists()
    # The real update still runs afterwards.
    assert tool.cmd_apply(chosen, "run", None) == 0
    assert docker.calls("run")[-1][docker.calls("run")[-1].index("spansh_delta.apply") + 1] == "apply"


def test_a_limit_is_passed_on_and_never_counts_as_the_whole_file(spansh, docker, tmp_path):
    spansh.publish(FILE, b"night one", MONDAY)
    chosen = settings(spansh, tmp_path / "galaxy")
    assert tool.cmd_apply(chosen, "run", 1000) == 0
    assert docker.calls("run")[0][-2:] == ["--limit", "1000"]
    assert not chosen.record.exists()
    assert tool.cmd_apply(chosen, "run", None) == 0
    assert len(docker.calls("run")) == 2 and chosen.record.exists()


def test_a_failed_update_is_reported_and_tried_again_next_time(spansh, docker, tmp_path, capsys):
    spansh.publish(FILE, b"night one", MONDAY)
    chosen = settings(spansh, tmp_path / "galaxy")
    docker.state["run_exit"]["local"] = 1
    docker.save()
    assert tool.cmd_apply(chosen, "run", None) == 1
    assert "FAILED: the update ended with code 1" in capsys.readouterr().out
    assert not chosen.record.exists()
    # A failed update is not a refused connection: the other route is not tried.
    assert len(docker.calls("run")) == 1

    docker.state["run_exit"]["local"] = 0
    docker.save()
    assert tool.cmd_apply(chosen, "run", None) == 0
    assert len(docker.calls("run")) == 2 and spansh.downloads() == [FILE]


def test_if_the_database_refuses_a_local_client_the_apps_own_settings_are_used(
        spansh, docker, tmp_path, capsys):
    spansh.publish(FILE, b"night one", MONDAY)
    chosen = settings(spansh, tmp_path / "galaxy")
    docker.state["run_exit"]["local"] = tool.EXIT_NO_CONNECTION
    docker.save()

    assert tool.cmd_apply(chosen, "run", None) == 0

    local, app = docker.calls("run")
    assert "container:edfinder-v3-phase4c-full-20260827_r5-postgres" in local
    assert app == [
        "run", "--rm", "--name", "edfinder-galaxy-update",
        "--network", "edfinder-v3-production", "--env-file", chosen.api_env_file,
        "--env", "EDFINDER_GALAXY_USE_DATABASE_URL=1",
        "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTHONUNBUFFERED=1",
        "--volume", "%s:/data:ro" % chosen.directory,
        "--security-opt", "no-new-privileges:true", "--pids-limit", "256",
        IMAGE, "python", "-m", "spansh_delta.apply", "apply", "/data/" + FILE,
        "--made-at", "2026-11-09T02:10:00+00:00",
    ]
    out = capsys.readouterr().out
    assert "Trying the app's own connection settings instead" in out
    assert chosen.record.exists()
    # The settings file is only pointed at: what is in it appears nowhere.
    assert SECRET not in out and SECRET not in docker.log_file.read_text()


def test_when_no_route_connects_the_databases_rules_are_shown(spansh, docker, tmp_path, capsys):
    spansh.publish(FILE, b"night one", MONDAY)
    chosen = settings(spansh, tmp_path / "galaxy")
    docker.state["run_exit"] = {"local": tool.EXIT_NO_CONNECTION, "app": tool.EXIT_NO_CONNECTION}
    docker.state["psql"] = {"pg_hba_file_rules": "local|all|all||trust\nhost|all|all|all|scram-sha-256"}
    docker.save()
    assert tool.cmd_apply(chosen, "check", None) == tool.EXIT_NO_CONNECTION
    out = capsys.readouterr().out
    assert len(docker.calls("run")) == 2
    assert "the database's rules for who may connect" in out
    assert "host   database=all user=all address=all method=scram-sha-256" in out
    assert "FAILED: the update could not connect to the database. Nothing was changed." in out
    assert not chosen.record.exists()


@pytest.mark.parametrize("route, flag", [("local", "container:"), ("app", "--env-file")])
def test_one_route_can_be_chosen_and_then_only_that_one_is_tried(spansh, docker, tmp_path, route, flag):
    spansh.publish(FILE, b"night one", MONDAY)
    chosen = settings(spansh, tmp_path / "galaxy", EDFINDER_GALAXY_CONNECT=route)
    docker.state["run_exit"] = {"local": tool.EXIT_NO_CONNECTION, "app": tool.EXIT_NO_CONNECTION}
    docker.save()
    assert tool.cmd_apply(chosen, "run", None) == tool.EXIT_NO_CONNECTION
    (only,) = docker.calls("run")
    assert any(part.startswith(flag) for part in only)


def test_the_apps_settings_file_must_exist_before_it_is_used(spansh, docker, tmp_path):
    spansh.publish(FILE, b"night one", MONDAY)
    chosen = settings(spansh, tmp_path / "galaxy", EDFINDER_GALAXY_CONNECT="app",
                      EDFINDER_API_ENV_FILE=str(tmp_path / "missing.env"))
    with pytest.raises(tool.UpdateError, match="settings file .* was not found"):
        tool.cmd_apply(chosen, "run", None)
    assert docker.calls("run") == []


def test_no_live_app_means_no_image_to_run_from(spansh, docker, tmp_path):
    spansh.publish(FILE, b"night one", MONDAY)
    docker.state["containers"] = {}
    docker.save()
    with pytest.raises(tool.UpdateError, match=r"live API container \(edfinder-api\) was not found"):
        tool.cmd_apply(settings(spansh, tmp_path / "galaxy"), "run", None)
    assert docker.calls("run") == []


def test_an_update_container_that_already_exists_blocks_a_second_one(spansh, docker, tmp_path):
    spansh.publish(FILE, b"night one", MONDAY)
    docker.state["containers"]["edfinder-galaxy-update"] = "running"
    docker.save()
    with pytest.raises(tool.UpdateError, match="already exists"):
        tool.cmd_apply(settings(spansh, tmp_path / "galaxy"), "run", None)
    assert docker.calls("run") == []


def test_two_launchers_cannot_run_at_once(spansh, docker, tmp_path):
    import fcntl

    spansh.publish(FILE, b"night one", MONDAY)
    chosen = settings(spansh, tmp_path / "galaxy")
    chosen.directory.mkdir(parents=True)
    with open(chosen.directory / "lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(tool.UpdateError, match="already running"):
            tool.cmd_apply(chosen, "run", None)
    assert docker.calls("run") == []


def test_cut_off_download_fails_and_nothing_is_run(spansh, docker, tmp_path):
    spansh.publish(FILE, b"night one" * 1000, MONDAY)
    spansh.cut_short.add(FILE)
    chosen = settings(spansh, tmp_path / "galaxy")
    with pytest.raises(tool.fetch.RefreshError, match="the download"):
        tool.cmd_apply(chosen, "run", None)
    assert not chosen.target.exists() and docker.calls("run") == []


def test_run_refuses_when_the_disk_is_too_full(spansh, docker, tmp_path, monkeypatch):
    spansh.publish(FILE, b"night one", MONDAY)
    monkeypatch.setattr(tool.shutil, "disk_usage", lambda path: tool.shutil._ntuple_diskusage(10, 9, 1))
    with pytest.raises(tool.UpdateError, match="not enough free disk space"):
        tool.cmd_apply(settings(spansh, tmp_path / "galaxy"), "run", None)
    assert spansh.downloads() == [] and docker.calls("run") == []


# -- status and the command line --------------------------------------------


def test_status_shows_the_last_update_the_queue_and_the_file(spansh, docker, tmp_path, capsys):
    spansh.publish(FILE, b"night one", MONDAY)
    chosen = settings(spansh, tmp_path / "galaxy")
    assert tool.cmd_apply(chosen, "run", None) == 0
    docker.state["psql"] = {
        "acquisition_kind = 'DELTA'": "SUCCEEDED|2026-11-10 04:41|galaxy_1day.json.gz|115929|61234|2701",
        "rescore_queue": "63935",
    }
    docker.save()
    capsys.readouterr()
    assert tool.cmd_status(chosen) == 0
    out = capsys.readouterr().out
    assert ("Last update: 2026-11-10 04:41 UTC, finished (galaxy_1day.json.gz). "
            "115929 systems read, 61234 changed, 2701 new.") in out
    assert "63935 systems are waiting for new scores." in out
    assert "galaxy_1day.json.gz: 0.0 GB, published " + MONDAY in out


def test_status_before_anything_has_happened(spansh, docker, tmp_path, capsys):
    docker.state["psql"] = {"acquisition_kind = 'DELTA'": "", "rescore_queue": None}
    docker.save()
    assert tool.cmd_status(settings(spansh, tmp_path / "galaxy")) == 0
    out = capsys.readouterr().out
    assert "No galaxy update has been recorded yet." in out
    assert "does not exist yet (migration 026)" in out
    assert "galaxy_1day.json.gz: not downloaded yet" in out


def test_main_prints_a_plain_failure_and_exits_1(spansh, docker, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EDFINDER_GALAXY_DIR", str(tmp_path / "galaxy"))
    monkeypatch.setenv("EDFINDER_GALAXY_URL", spansh.origin + "/" + FILE)  # not published
    assert tool.main(["run"]) == 1
    assert "FAILED: " in capsys.readouterr().out
    assert tool.main(["run", "--limit", "0"]) == 1
    assert "--limit must be 1 or more" in capsys.readouterr().out


# -- the timer and service files --------------------------------------------


def test_the_timer_runs_twice_a_day_well_after_spansh_publishes():
    timer = (ROOT / "deploy" / "edfinder-galaxy-update.timer").read_text(encoding="utf-8")
    settings_lines = [line for line in timer.splitlines() if line and not line.startswith("#")]
    assert "OnCalendar=*-*-* 04,16:30:00 UTC" in settings_lines
    assert "Persistent=true" in settings_lines and "WantedBy=timers.target" in settings_lines
    assert len([line for line in settings_lines if line.startswith("OnCalendar=")]) == 1


def test_the_service_runs_the_launcher_where_it_is_installed():
    service = (ROOT / "deploy" / "edfinder-galaxy-update.service").read_text(encoding="utf-8")
    assert "ExecStart=/usr/bin/python3 /opt/edfinder/galaxy_update.py run" in service.splitlines()
    assert "Type=oneshot" in service.splitlines()
