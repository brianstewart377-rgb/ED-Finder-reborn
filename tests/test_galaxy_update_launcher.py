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
    module = args[args.index("-m") + 1]
    print("%%s (%%s) by the %%s route" %% (module, args[args.index("-m") + 2], route))
    code = state.get("module_exit", {}).get(module, state["run_exit"][route])
    if module == "spansh_delta.recent" and code == 0:
        folder = args[args.index("--volume") + 1].split(":")[0]
        name = args[args.index("-m") + 4][len("/data/"):]
        with open(os.path.join(folder, name), "wb") as saved:
            saved.write(b"the systems changed since then")
        with open(os.path.join(folder, name + ".json"), "w") as about:
            json.dump({"since": args[args.index("--since") + 1], "systems_read": 1000,
                       "systems_saved": state.get("saved", 42),
                       "stopped_early": "--limit" in args}, about)
    sys.exit(code)
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
    # No fixed file: each run chooses Spansh's 1-day, 7-day or 32-day file.
    assert chosen.url is None and chosen.source == "https://downloads.spansh.co.uk/"
    assert chosen.usual_file == "galaxy_7days.json.gz"
    assert chosen.path(chosen.usual_file) == Path("/var/lib/edfinder-galaxy/galaxy_7days.json.gz")
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
    ("EDFINDER_GALAXY_SCORES", "yes", "must be on or off"),
])
def test_bad_settings_are_refused(key, value, message):
    with pytest.raises(tool.UpdateError, match=message):
        tool.Settings(environ={key: value}, conf={})


# -- fetching and running ---------------------------------------------------


def test_run_downloads_the_file_and_runs_the_update_from_the_live_image(spansh, docker, tmp_path, capsys):
    spansh.publish(FILE, b"night one" * 100, MONDAY)
    chosen = settings(spansh, tmp_path / "galaxy")

    assert tool.cmd_apply(chosen, "run", None) == 0

    assert chosen.path(FILE).read_bytes() == b"night one" * 100
    (run,) = docker.calls("run")
    assert run == [
        "run", "--rm", "--init", "--name", "edfinder-galaxy-update",
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
    assert stat.S_IMODE(chosen.path(FILE).stat().st_mode) == 0o644
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

    assert spansh.downloads() == [FILE] and chosen.path(FILE).read_bytes() == b"night one"
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


def modules(docker) -> list[tuple[str, str]]:
    return [(call[call.index("-m") + 1], call[call.index("-m") + 2]) for call in docker.calls("run")]


def test_scoring_is_off_unless_switched_on(spansh, docker, tmp_path):
    spansh.publish(FILE, b"night one", MONDAY)
    chosen = settings(spansh, tmp_path / "galaxy")
    assert not chosen.scores
    assert tool.cmd_apply(chosen, "run", None) == 0
    assert modules(docker) == [("spansh_delta.apply", "apply")]


def test_with_scoring_on_a_run_updates_the_galaxy_then_scores_the_waiting_list(spansh, docker, tmp_path, capsys):
    spansh.publish(FILE, b"night one", MONDAY)
    chosen = settings(spansh, tmp_path / "galaxy", EDFINDER_GALAXY_SCORES="on")
    assert tool.cmd_apply(chosen, "run", None) == 0
    assert modules(docker) == [("spansh_delta.apply", "apply"), ("spansh_delta.scores", "apply")]
    # The scoring is given no file: it works from the database.
    scoring = docker.calls("run")[-1]
    assert scoring[scoring.index("-m"):] == ["-m", "spansh_delta.scores", "apply"]
    assert tool.fetch.read_record(chosen.record)["applied"]["modified"] == MONDAY
    assert "Running the scoring from " + IMAGE in capsys.readouterr().out
    # Nothing new from Spansh: the galaxy is not updated again, but the
    # waiting list is looked at every time.
    assert tool.cmd_apply(chosen, "run", None) == 0
    assert modules(docker) == [("spansh_delta.apply", "apply"), ("spansh_delta.scores", "apply"),
                               ("spansh_delta.scores", "apply")]
    assert "Looking at the waiting list for new scores" in capsys.readouterr().out
    assert spansh.downloads() == [FILE]


def test_if_the_update_fails_nothing_is_scored(spansh, docker, tmp_path):
    spansh.publish(FILE, b"night one", MONDAY)
    chosen = settings(spansh, tmp_path / "galaxy", EDFINDER_GALAXY_SCORES="on")
    docker.state["module_exit"] = {"spansh_delta.apply": 1}
    docker.save()
    assert tool.cmd_apply(chosen, "run", None) == 1
    assert modules(docker) == [("spansh_delta.apply", "apply")]
    assert not chosen.record.exists()


def test_a_night_when_scoring_fails_is_made_up_by_the_next_run_even_after_a_new_file(
        spansh, docker, tmp_path, capsys):
    spansh.publish(FILE, b"night one", MONDAY)
    chosen = settings(spansh, tmp_path / "galaxy", EDFINDER_GALAXY_SCORES="on")
    docker.state["module_exit"] = {"spansh_delta.scores": 1}
    docker.save()
    assert tool.cmd_apply(chosen, "run", None) == 1
    assert "FAILED: the scoring ended with code 1" in capsys.readouterr().out
    # The update itself is recorded as done; what it changed is on the waiting list.
    assert tool.fetch.read_record(chosen.record)["applied"]["modified"] == MONDAY

    # Spansh publishes the next night's file before the scoring has worked.
    # Nothing is lost: the waiting list is in the database, not in the old file.
    spansh.publish(FILE, b"night two!", TUESDAY)
    docker.state["module_exit"] = {}
    docker.save()
    assert tool.cmd_apply(chosen, "run", None) == 0
    assert modules(docker)[2:] == [("spansh_delta.apply", "apply"), ("spansh_delta.scores", "apply")]
    assert tool.fetch.read_record(chosen.record)["applied"]["modified"] == TUESDAY


def test_score_by_hand_needs_no_file_and_fetches_nothing(spansh, docker, tmp_path):
    chosen = settings(spansh, tmp_path / "galaxy")
    assert tool.cmd_apply(chosen, "score", 500) == 0
    scoring = docker.calls("run")[-1]
    assert scoring[scoring.index("-m"):] == ["-m", "spansh_delta.scores", "apply", "--limit", "500"]
    assert modules(docker) == [("spansh_delta.scores", "apply")]
    assert spansh.downloads() == [] and not chosen.record.exists()


def test_reach_by_hand_needs_no_file_and_works_it_out_in_the_container(spansh, docker, tmp_path, capsys):
    chosen = settings(spansh, tmp_path / "galaxy")
    assert tool.cmd_apply(chosen, "reach", None) == 0
    worked = docker.calls("run")[-1]
    assert worked[worked.index("-m"):] == ["-m", "spansh_delta.reach", "rebuild"]
    assert modules(docker) == [("spansh_delta.reach", "rebuild")]
    assert spansh.downloads() == [] and not chosen.record.exists()
    assert "Running the work on reach from " + IMAGE in capsys.readouterr().out
    docker.state["run_exit"]["local"] = 2
    docker.save()
    assert tool.cmd_apply(chosen, "reach", None) == 2
    assert "FAILED: the work on reach ended with code 2" in capsys.readouterr().out


def test_colony_types_by_hand_need_no_file_and_are_scored_in_the_container(
        spansh, docker, tmp_path, capsys):
    chosen = settings(spansh, tmp_path / "galaxy")
    assert tool.cmd_apply(chosen, "colony-types", None) == 0
    worked = docker.calls("run")[-1]
    assert worked[worked.index("-m"):] == ["-m", "spansh_delta.colony_types", "apply"]
    assert spansh.downloads() == [] and not chosen.record.exists()
    assert "Running the colony type scoring from " + IMAGE in capsys.readouterr().out
    # A limit, for timing it on the first systems.
    assert tool.cmd_apply(chosen, "colony-types", 100000) == 0
    worked = docker.calls("run")[-1]
    assert worked[worked.index("-m"):] == [
        "-m", "spansh_delta.colony_types", "apply", "--limit", "100000"]
    docker.state["run_exit"]["local"] = 2
    docker.save()
    assert tool.cmd_apply(chosen, "colony-types", None) == 2
    assert "FAILED: the colony type scoring ended with code 2" in capsys.readouterr().out


def test_the_speed_trial_needs_no_file_and_runs_in_the_container(spansh, docker, tmp_path, capsys):
    chosen = settings(spansh, tmp_path / "galaxy")
    assert tool.cmd_apply(chosen, "speed-trial", None) == 0
    worked = docker.calls("run")[-1]
    assert worked[worked.index("-m"):] == ["-m", "spansh_delta.speed_trial", "run"]
    assert spansh.downloads() == [] and not chosen.record.exists()
    assert "Running the speed trial from " + IMAGE in capsys.readouterr().out
    # Another sample size.
    assert tool.cmd_apply(chosen, "speed-trial", 50000) == 0
    worked = docker.calls("run")[-1]
    assert worked[worked.index("-m"):] == ["-m", "spansh_delta.speed_trial", "run", "--limit", "50000"]
    docker.state["run_exit"]["local"] = 1
    docker.save()
    assert tool.cmd_apply(chosen, "speed-trial", None) == 1
    assert "FAILED: the speed trial ended with code 1" in capsys.readouterr().out


def test_main_knows_the_speed_trial(spansh, docker, tmp_path, monkeypatch):
    monkeypatch.setenv("EDFINDER_GALAXY_DIR", str(tmp_path / "galaxy"))
    monkeypatch.setenv("EDFINDER_GALAXY_SOURCE", spansh.origin + "/")
    assert tool.main(["speed-trial", "--limit", "2000"]) == 0
    assert docker.calls("run")[-1][-4:] == ["spansh_delta.speed_trial", "run", "--limit", "2000"]
    assert spansh.downloads() == []


# Filling in what bodies orbit, once (spansh_delta.parents): read from Spansh's
# whole-galaxy file as it downloads, inside the job, so nothing is kept here.
WHOLE_GALAXY = "galaxy.json.gz"


@pytest.mark.parametrize("command, mode, said", [
    ("check-parents", "check", "Running the check from "),
    ("fill-parents", "apply", "Running the fill-in of parents from "),
])
def test_parents_are_read_from_the_whole_galaxy_file_inside_the_job(
        spansh, docker, tmp_path, capsys, command, mode, said):
    chosen = settings(spansh, tmp_path / "galaxy", EDFINDER_GALAXY_URL="",
                      EDFINDER_GALAXY_SOURCE=spansh.origin + "/")
    assert tool.cmd_apply(chosen, command, None) == 0
    job = docker.calls("run")[-1]
    whole = spansh.origin + "/" + WHOLE_GALAXY
    assert job[job.index("-m"):] == ["-m", "spansh_delta.parents", mode, whole]
    assert modules(docker) == [("spansh_delta.parents", mode)]
    out = capsys.readouterr().out
    assert "Reading %s as it downloads." % whole in out and said + IMAGE in out
    # Nothing is fetched here, and it never counts as an update applied.
    assert spansh.requests == [] and not chosen.record.exists()


def test_parents_take_a_limit_and_another_file_given_by_hand(spansh, docker, tmp_path):
    chosen = settings(spansh, tmp_path / "galaxy")  # EDFINDER_GALAXY_URL: the 1-day file
    assert tool.cmd_apply(chosen, "check-parents", 1_000_000) == 0
    job = docker.calls("run")[-1]
    assert job[job.index("-m"):] == ["-m", "spansh_delta.parents", "check",
                                     spansh.origin + "/" + FILE, "--limit", "1000000"]
    assert spansh.requests == []


def test_a_failed_fill_in_of_parents_says_so(spansh, docker, tmp_path, capsys):
    docker.state["module_exit"] = {"spansh_delta.parents": 1}
    docker.save()
    chosen = settings(spansh, tmp_path / "galaxy")
    assert tool.cmd_apply(chosen, "fill-parents", None) == 1
    assert "FAILED: the fill-in ended with code 1; its own messages are above." in capsys.readouterr().out


def test_main_knows_the_parents_commands(spansh, docker, tmp_path, monkeypatch):
    monkeypatch.setenv("EDFINDER_GALAXY_DIR", str(tmp_path / "galaxy"))
    monkeypatch.setenv("EDFINDER_GALAXY_SOURCE", spansh.origin + "/")
    monkeypatch.setenv("EDFINDER_GALAXY_URL", "")
    assert tool.main(["check-parents", "--limit", "10"]) == 0
    assert tool.main(["fill-parents"]) == 0
    jobs = docker.calls("run")
    assert jobs[0][-4:] == ["check", spansh.origin + "/" + WHOLE_GALAXY, "--limit", "10"]
    assert jobs[1][-2:] == ["apply", spansh.origin + "/" + WHOLE_GALAXY]


# Catching up with Spansh's whole-galaxy file (spansh_delta.recent, then the
# update): the changed systems are saved here, then applied, then scored.
IMPORTED = "acquisition_kind = 'BULK_SNAPSHOT'"
SAVED = "galaxy_since_2026-08-22.json.gz"


def catching_up(spansh: Spansh, directory: Path, **extra: str):
    """Settings with no fixed file: catch-up reads Spansh's whole-galaxy file."""
    return settings(spansh, directory, EDFINDER_GALAXY_URL="",
                    EDFINDER_GALAXY_SOURCE=spansh.origin + "/", **extra)


def database_imported(docker, imported: str | None) -> None:
    docker.state["psql"] = {IMPORTED: imported or ""}
    docker.save()


def jobs(docker) -> list[list[str]]:
    return [call[call.index("-m") + 1:] for call in docker.calls("run")]


def test_catch_up_saves_what_changed_since_the_import_then_applies_it(spansh, docker, tmp_path, capsys):
    spansh.publish(WHOLE_GALAXY, b"the whole galaxy", TONIGHT)
    database_imported(docker, "2026-08-24")
    chosen = catching_up(spansh, tmp_path / "galaxy")

    assert tool.cmd_apply(chosen, "catch-up", None) == 0

    whole = spansh.origin + "/" + WHOLE_GALAXY
    assert jobs(docker) == [
        ["spansh_delta.recent", "save", whole, "/data/" + SAVED, "--since", "2026-08-22"],
        ["spansh_delta.apply", "apply", "/data/" + SAVED, "--total", "42",
         "--made-at", "2026-11-12T03:05:00+00:00"],
    ]
    save, apply = docker.calls("run")
    # Only the saving job may write here.
    assert "%s:/data" % chosen.directory in save and "%s:/data:ro" % chosen.directory in apply
    # The whole-galaxy file is read inside the job as it downloads, never here;
    # the saved file is removed once applied.
    assert spansh.downloads() == [] and not list(chosen.directory.glob("galaxy_since_*"))
    out = capsys.readouterr().out
    assert ("The galaxy was imported from Spansh's file of 24 Aug 2026. Taking every system "
            "Spansh has changed since 22 Aug 2026.") in out
    assert "Applying the 42 systems saved." in out
    assert "now up to date with Spansh's file of 12 Nov 2026 03:05 UTC" in out


def test_catch_up_carries_on_from_the_saved_file_after_the_update_stopped(
        spansh, docker, tmp_path, capsys):
    spansh.publish(WHOLE_GALAXY, b"the whole galaxy", TONIGHT)
    database_imported(docker, "2026-08-24")
    docker.state["module_exit"] = {"spansh_delta.apply": 1}
    docker.save()
    chosen = catching_up(spansh, tmp_path / "galaxy")
    assert tool.cmd_apply(chosen, "catch-up", None) == 1
    out = capsys.readouterr().out
    assert "What was applied stays. Running catch-up again carries on from the saved file." in out
    assert chosen.path(SAVED).is_file()
    asked = list(spansh.requests)

    docker.state["module_exit"] = {}
    docker.save()
    assert tool.cmd_apply(chosen, "catch-up", None) == 0
    # The second run did not read Spansh's file again: it applied the saved one.
    assert [job[0] for job in jobs(docker)] == ["spansh_delta.recent", "spansh_delta.apply",
                                                 "spansh_delta.apply"]
    assert spansh.requests == asked
    assert "Carrying on from the systems saved before: 42 changed since 22 Aug 2026" in capsys.readouterr().out
    assert not list(chosen.directory.glob("galaxy_since_*"))


def test_a_failed_first_half_applies_nothing_and_the_next_run_starts_afresh(
        spansh, docker, tmp_path, capsys):
    spansh.publish(WHOLE_GALAXY, b"the whole galaxy", TONIGHT)
    database_imported(docker, "2026-08-24")
    docker.state["module_exit"] = {"spansh_delta.recent": 1}
    docker.save()
    chosen = catching_up(spansh, tmp_path / "galaxy")
    assert tool.cmd_apply(chosen, "catch-up", None) == 1
    assert "FAILED: the first half of the catch-up ended with code 1" in capsys.readouterr().out
    assert [job[0] for job in jobs(docker)] == ["spansh_delta.recent"]

    docker.state["module_exit"] = {}
    docker.save()
    assert tool.cmd_apply(chosen, "catch-up", None) == 0
    assert [job[0] for job in jobs(docker)] == ["spansh_delta.recent", "spansh_delta.recent",
                                                 "spansh_delta.apply"]


def test_catch_up_from_a_day_given_by_hand_and_none_without_an_import(spansh, docker, tmp_path):
    spansh.publish(WHOLE_GALAXY, b"the whole galaxy", TONIGHT)
    database_imported(docker, None)
    chosen = catching_up(spansh, tmp_path / "galaxy")
    with pytest.raises(tool.UpdateError, match="no import of the whole galaxy.*--since YYYY-MM-DD"):
        tool.cmd_apply(chosen, "catch-up", None)
    assert docker.calls("run") == []

    assert tool.cmd_apply(chosen, "catch-up", None, tool.date(2026, 10, 1)) == 0
    assert jobs(docker)[0][-2:] == ["--since", "2026-10-01"]
    assert jobs(docker)[1][2] == "/data/galaxy_since_2026-10-01.json.gz"


def test_a_catch_up_trial_applies_nothing_and_keeps_nothing(spansh, docker, tmp_path, capsys):
    spansh.publish(WHOLE_GALAXY, b"the whole galaxy", TONIGHT)
    database_imported(docker, "2026-08-24")
    chosen = catching_up(spansh, tmp_path / "galaxy")
    assert tool.cmd_apply(chosen, "catch-up", 1_000_000) == 0
    trial = "/data/galaxy_since_2026-08-22.trial.json.gz"
    assert jobs(docker) == [["spansh_delta.recent", "save", spansh.origin + "/" + WHOLE_GALAXY, trial,
                             "--since", "2026-08-22", "--limit", "1000000"]]
    assert not list(chosen.directory.glob("galaxy_since_*"))
    assert "Nothing will be applied." in capsys.readouterr().out


def test_catch_up_gives_new_scores_afterwards_when_scoring_is_on(spansh, docker, tmp_path):
    spansh.publish(WHOLE_GALAXY, b"the whole galaxy", TONIGHT)
    database_imported(docker, "2026-08-24")
    chosen = catching_up(spansh, tmp_path / "galaxy", EDFINDER_GALAXY_SCORES="on")
    assert tool.cmd_apply(chosen, "catch-up", None) == 0
    assert [job[:2] for job in jobs(docker)] == [
        ["spansh_delta.recent", "save"], ["spansh_delta.apply", "apply"], ["spansh_delta.scores", "apply"]]


def test_main_knows_catch_up_and_since_is_for_it_only(spansh, docker, tmp_path, monkeypatch, capsys):
    spansh.publish(WHOLE_GALAXY, b"the whole galaxy", TONIGHT)
    database_imported(docker, "2026-08-24")
    monkeypatch.setenv("EDFINDER_GALAXY_DIR", str(tmp_path / "galaxy"))
    monkeypatch.setenv("EDFINDER_GALAXY_SOURCE", spansh.origin + "/")
    monkeypatch.setenv("EDFINDER_GALAXY_URL", "")
    assert tool.main(["catch-up", "--since", "2026-09-01"]) == 0
    assert jobs(docker)[0][-2:] == ["--since", "2026-09-01"]
    assert tool.main(["run", "--since", "2026-09-01"]) == 1
    assert "FAILED: --since is for catch-up only" in capsys.readouterr().out
    assert tool.main(["catch-up", "--since", "1 September"]) == 1
    assert "is not a day like 2026-08-22" in capsys.readouterr().out


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
        "run", "--rm", "--init", "--name", "edfinder-galaxy-update",
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
    assert "FAILED: the check could not connect to the database. Nothing was changed." in out
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
    assert not chosen.path(FILE).exists() and docker.calls("run") == []


def test_run_refuses_when_the_disk_is_too_full(spansh, docker, tmp_path, monkeypatch):
    spansh.publish(FILE, b"night one", MONDAY)
    monkeypatch.setattr(tool.shutil, "disk_usage", lambda path: tool.shutil._ntuple_diskusage(10, 9, 1))
    with pytest.raises(tool.UpdateError, match="not enough free disk space"):
        tool.cmd_apply(settings(spansh, tmp_path / "galaxy"), "run", None)
    assert spansh.downloads() == [] and docker.calls("run") == []


# -- choosing the file (no fixed file given) ---------------------------------

WEEK = "galaxy_7days.json.gz"
MONTH = "galaxy_1month.json.gz"
TONIGHT = "Thu, 12 Nov 2026 03:05:00 GMT"
COVERED = "max(r.effective_at)"
# Only in the question about the last 7-day or bigger file: a stand-in answers
# the first of its needles found in a statement, so this one goes first.
SWEPT = "<> 'galaxy_1day.json.gz'"


def choosing(spansh: Spansh, directory: Path, **extra: str):
    """Settings with no fixed file: each run chooses."""
    return settings(spansh, directory, EDFINDER_GALAXY_URL="",
                    EDFINDER_GALAXY_SOURCE=spansh.origin + "/", **extra)


def database_says(docker, last_update: str | None, last_week_or_bigger: str | None = None) -> None:
    """The last finished update applied Spansh's files of ``last_update``
    (UTC); None: no update has finished. The last 7-day or bigger file applied
    was made at ``last_week_or_bigger``, or if not given, the same as the last
    update's."""
    replies = {} if last_week_or_bigger is None else {SWEPT: last_week_or_bigger}
    replies[COVERED] = last_update or ""
    docker.state["psql"] = replies
    docker.save()


def applied(docker) -> list[str]:
    return [call[call.index("-m") + 3] for call in docker.calls("run")
            if call[call.index("-m") + 1] == "spansh_delta.apply"]


def test_when_spansh_cannot_be_asked_about_its_1_day_file_the_7_day_file_does(
        spansh, docker, tmp_path, capsys):
    # The tests further up that choose leave the 1-day file out too.
    spansh.publish(WEEK, b"seven days", TONIGHT)
    spansh.publish(MONTH, b"thirty-two days", TONIGHT)
    chosen = choosing(spansh, tmp_path / "galaxy")
    database_says(docker, "2026-11-11 03:19:43")

    assert tool.cmd_apply(chosen, "run", None) == 0

    assert spansh.downloads() == [WEEK] and applied(docker) == ["/data/" + WEEK]
    (run,) = docker.calls("run")
    assert run[-2:] == ["--made-at", "2026-11-12T03:05:00+00:00"]
    assert tool.fetch.read_record(chosen.record)["applied"]["modified"] == TONIGHT
    out = capsys.readouterr().out
    assert ("Going by Spansh's 7-day file alone: asking about its 1-day file failed (%s/%s: "
            "could not be reached (the server answered 404))." % (spansh.origin, FILE)) in out
    assert "Downloading galaxy_7days.json.gz" in out


def test_a_file_made_up_to_6_days_after_the_last_one_is_still_the_7_day_file(spansh, docker, tmp_path):
    spansh.publish(WEEK, b"seven days", TONIGHT)
    spansh.publish(MONTH, b"thirty-two days", TONIGHT)
    chosen = choosing(spansh, tmp_path / "galaxy")
    database_says(docker, "2026-11-06 03:05:00")  # exactly 6 days before
    assert tool.cmd_apply(chosen, "run", None) == 0
    assert spansh.downloads() == [WEEK]


def test_after_more_than_6_days_away_the_32_day_file_is_taken(spansh, docker, tmp_path, capsys):
    spansh.publish(WEEK, b"seven days", TONIGHT)
    spansh.publish(MONTH, b"thirty-two days", TONIGHT)
    chosen = choosing(spansh, tmp_path / "galaxy")
    database_says(docker, "2026-11-06 03:04:59")  # one second more than 6 days

    assert tool.cmd_apply(chosen, "run", None) == 0

    assert spansh.downloads() == [MONTH] and applied(docker) == ["/data/" + MONTH]
    out = capsys.readouterr().out
    assert ("The last update applied Spansh's files of 06 Nov 2026 03:04 UTC, more than 6 days "
            "ago. Taking the 32-day file instead of the 7-day one.") in out
    assert tool.fetch.read_record(chosen.record)["applied"]["url"].endswith("/" + MONTH)


def test_up_to_31_days_away_is_the_32_day_file_and_more_stops_without_changing_anything(
        spansh, docker, tmp_path, capsys):
    spansh.publish(WEEK, b"seven days", TONIGHT)
    spansh.publish(MONTH, b"thirty-two days", TONIGHT)
    chosen = choosing(spansh, tmp_path / "galaxy")
    database_says(docker, "2026-10-12 03:05:00")  # exactly 31 days before
    assert tool.cmd_apply(chosen, "run", None) == 0
    assert applied(docker) == ["/data/" + MONTH]

    database_says(docker, "2026-10-12 03:04:59")
    with pytest.raises(tool.UpdateError) as refused:
        tool.cmd_apply(chosen, "run", None)
    message = str(refused.value)
    assert "12 Oct 2026 03:04 UTC, more than 31 days ago" in message
    assert "only its whole-galaxy file" in message
    assert "python3 galaxy_update.py catch-up" in message
    assert "Nothing was changed." in message
    assert applied(docker) == ["/data/" + MONTH]  # no second update was started


def test_with_neither_an_import_nor_an_update_in_the_database_it_will_not_guess(
        spansh, docker, tmp_path):
    spansh.publish(WEEK, b"seven days", TONIGHT)
    chosen = choosing(spansh, tmp_path / "galaxy")
    database_says(docker, None)
    for mode in ("run", "check"):
        with pytest.raises(tool.UpdateError, match="records neither an import of the whole galaxy"):
            tool.cmd_apply(chosen, mode, None)
    assert spansh.downloads() == [] and docker.calls("run") == []


def test_before_the_first_update_the_file_is_chosen_from_the_import():
    # The time the choice starts from is the last finished update's file, or
    # the whole-galaxy file the galaxy was imported from (tested against the
    # real schema in tests/test_catch_up.py).
    assert "acquisition_kind IN ('DELTA', 'BULK_SNAPSHOT')" in tool.COVERED_SQL
    assert "run_state = 'SUCCEEDED'" in tool.COVERED_SQL


# Most nights the 1-day file. The 7-day file after a missed night, and every
# sixth night whatever happens, to fill any gap between the 1-day files.

def tonights_files(spansh: Spansh) -> None:
    for name in (FILE, WEEK, MONTH):
        spansh.publish(name, b"tonight's " + name.encode(), TONIGHT)


def test_the_night_after_an_update_takes_the_1_day_file(spansh, docker, tmp_path, capsys):
    tonights_files(spansh)
    chosen = choosing(spansh, tmp_path / "galaxy")
    database_says(docker, "2026-11-11 03:19:43", last_week_or_bigger="2026-11-08 02:40:00")

    assert tool.cmd_apply(chosen, "run", None) == 0

    assert spansh.downloads() == [FILE] and applied(docker) == ["/data/" + FILE]
    (run,) = docker.calls("run")
    assert run[-2:] == ["--made-at", "2026-11-12T03:05:00+00:00"]
    assert tool.fetch.read_record(chosen.record)["applied"]["url"].endswith("/" + FILE)
    out = capsys.readouterr().out
    assert "Downloading galaxy_1day.json.gz" in out and "Taking" not in out


def test_up_to_a_day_and_a_half_on_is_the_night_after_and_more_is_a_missed_night(
        spansh, docker, tmp_path, capsys):
    tonights_files(spansh)
    database_says(docker, "2026-11-10 15:05:00")  # exactly 36 hours before tonight's files
    assert tool.cmd_apply(choosing(spansh, tmp_path / "after"), "run", None) == 0
    assert spansh.downloads() == [FILE]

    database_says(docker, "2026-11-10 15:04:59")  # one second more
    capsys.readouterr()
    assert tool.cmd_apply(choosing(spansh, tmp_path / "missed"), "run", None) == 0
    assert spansh.downloads() == [FILE, WEEK] and applied(docker) == ["/data/" + FILE, "/data/" + WEEK]
    assert ("The last update applied Spansh's files of 10 Nov 2026 15:04 UTC, more than a day "
            "and a half before tonight's, so a night was missed. Taking the 7-day file instead "
            "of the 1-day one.") in capsys.readouterr().out


def test_after_more_missed_nights_the_7_day_and_32_day_files_are_chosen_as_before(
        spansh, docker, tmp_path, capsys):
    tonights_files(spansh)
    database_says(docker, "2026-11-06 03:05:00")  # 6 days: still the 7-day file
    assert tool.cmd_apply(choosing(spansh, tmp_path / "six"), "run", None) == 0
    database_says(docker, "2026-11-06 03:04:59")  # more: the 32-day file
    assert tool.cmd_apply(choosing(spansh, tmp_path / "more"), "run", None) == 0
    assert spansh.downloads() == [WEEK, MONTH]
    out = capsys.readouterr().out
    assert "Taking the 32-day file instead of the 7-day one." in out
    assert out.count("Taking the 7-day file instead of the 1-day one.") == 1  # the first run


def test_the_sixth_night_after_the_last_7_day_file_takes_the_7_day_file_again(
        spansh, docker, tmp_path, capsys):
    tonights_files(spansh)
    # Five and a half days on: the sixth night.
    database_says(docker, "2026-11-11 03:19:43", last_week_or_bigger="2026-11-06 15:05:00")
    assert tool.cmd_apply(choosing(spansh, tmp_path / "sixth"), "run", None) == 0
    assert spansh.downloads() == [WEEK]
    assert ("The last 7-day or bigger file applied was Spansh's of 06 Nov 2026 15:05 UTC, six or "
            "more nights ago. Taking the 7-day file tonight instead of the 1-day one, to fill any "
            "gap between the 1-day files.") in capsys.readouterr().out

    # A second less: the fifth night, so the 1-day file.
    database_says(docker, "2026-11-11 03:19:43", last_week_or_bigger="2026-11-06 15:05:01")
    assert tool.cmd_apply(choosing(spansh, tmp_path / "fifth"), "run", None) == 0
    assert spansh.downloads() == [WEEK, FILE]


def test_the_16_30_run_after_the_1_day_file_finds_nothing_new(spansh, docker, tmp_path, capsys):
    tonights_files(spansh)
    chosen = choosing(spansh, tmp_path / "galaxy", EDFINDER_GALAXY_SCORES="on")
    database_says(docker, "2026-11-11 03:19:43")
    assert tool.cmd_apply(chosen, "run", None) == 0

    # The database now dates the last update by tonight's files. The 7-day
    # file is that same night's, even if Spansh finished it a little later.
    database_says(docker, "2026-11-12 03:05:00", last_week_or_bigger="2026-11-11 03:19:43")
    spansh.publish(WEEK, b"tonight's seven days", "Thu, 12 Nov 2026 03:40:00 GMT")
    capsys.readouterr()
    assert tool.cmd_apply(chosen, "run", None) == 0
    assert spansh.downloads() == [FILE] and applied(docker) == ["/data/" + FILE]
    assert "Spansh has published nothing new since the last update." in capsys.readouterr().out
    assert modules(docker)[-1] == ("spansh_delta.scores", "apply")


def test_the_16_30_run_after_the_7_day_file_finds_nothing_new(spansh, docker, tmp_path, capsys):
    tonights_files(spansh)
    chosen = choosing(spansh, tmp_path / "galaxy")
    database_says(docker, "2026-11-11 03:19:43", last_week_or_bigger="2026-11-05 03:00:00")
    assert tool.cmd_apply(chosen, "run", None) == 0
    assert spansh.downloads() == [WEEK]

    database_says(docker, "2026-11-12 03:05:00")
    spansh.publish(FILE, b"tonight's one day", "Thu, 12 Nov 2026 03:06:00 GMT")
    capsys.readouterr()
    assert tool.cmd_apply(chosen, "run", None) == 0
    assert spansh.downloads() == [WEEK] and applied(docker) == ["/data/" + WEEK]
    assert "Spansh has published nothing new since the last update." in capsys.readouterr().out


def test_a_new_1_day_file_without_a_new_7_day_file(spansh, docker, tmp_path):
    spansh.publish(FILE, b"tonight's one day", TONIGHT)
    spansh.publish(WEEK, b"last night's seven days", "Wed, 11 Nov 2026 03:19:43 GMT")
    # It follows on from the last update: taken, even on the sixth night.
    database_says(docker, "2026-11-11 03:19:43", last_week_or_bigger="2026-11-01 03:00:00")
    assert tool.cmd_apply(choosing(spansh, tmp_path / "follows"), "run", None) == 0
    assert spansh.downloads() == [FILE]

    # A night was missed before it, and only a 7-day file covers that night.
    spansh.publish(WEEK, b"older seven days", "Tue, 10 Nov 2026 03:00:00 GMT")
    database_says(docker, "2026-11-10 03:00:00")
    with pytest.raises(tool.UpdateError) as refused:
        tool.cmd_apply(choosing(spansh, tmp_path / "missed"), "run", None)
    message = str(refused.value)
    assert "does not follow on from the last update (its files of 10 Nov 2026 03:00 UTC)" in message
    assert "a night was missed in between" in message and "Nothing was changed" in message
    assert spansh.downloads() == [FILE] and applied(docker) == ["/data/" + FILE]


def test_the_stand_in_database_can_tell_the_two_questions_apart():
    # What they mean is tested against the real schema in tests/test_catch_up.py.
    assert SWEPT in tool.SWEPT_SQL and SWEPT not in tool.COVERED_SQL
    assert COVERED in tool.SWEPT_SQL and COVERED in tool.COVERED_SQL


# A file given by hand must reach back to the last update: otherwise what
# Spansh changed in between is left out for good. That is how 24 August to
# 6 November 2026 went missing on the Pi.

def by_hand(spansh: Spansh, directory: Path, name: str):
    return settings(spansh, directory, EDFINDER_GALAXY_URL="%s/%s" % (spansh.origin, name))


def test_a_file_given_by_hand_that_does_not_reach_back_far_enough_is_refused(spansh, docker, tmp_path):
    spansh.publish(FILE, b"one day", TONIGHT)
    spansh.publish(WEEK, b"seven days", TONIGHT)
    database_says(docker, "2026-11-11 03:05:00")  # a day before tonight's files
    for name in (FILE, WEEK):
        chosen = by_hand(spansh, tmp_path / name, name)
        if name == WEEK:
            database_says(docker, "2026-11-05 03:04:59")  # more than 6 days before
        with pytest.raises(tool.UpdateError) as refused:
            tool.cmd_apply(chosen, "run", None)
        message = str(refused.value)
        assert "does not reach back that far" in message and "left out for good" in message
        assert "python3 galaxy_update.py catch-up" in message and "Nothing was changed." in message
    assert spansh.downloads() == [] and docker.calls("run") == []


def test_a_file_given_by_hand_that_reaches_back_far_enough_is_applied(spansh, docker, tmp_path):
    spansh.publish(WEEK, b"seven days", TONIGHT)
    spansh.publish(tool.FULL_FILE, b"the whole galaxy", TONIGHT)
    database_says(docker, "2026-11-06 03:05:00")  # exactly 6 days before
    assert tool.cmd_apply(by_hand(spansh, tmp_path / "week", WEEK), "run", None) == 0
    # The whole-galaxy file reaches back to the beginning.
    database_says(docker, "2026-08-24 01:00:00")
    assert tool.cmd_apply(by_hand(spansh, tmp_path / "whole", tool.FULL_FILE), "run", None) == 0
    assert applied(docker) == ["/data/" + WEEK, "/data/" + tool.FULL_FILE]


def test_checking_a_file_given_by_hand_is_never_refused(spansh, docker, tmp_path):
    # check writes nothing, so it can leave nothing out.
    spansh.publish(FILE, b"one day", TONIGHT)
    database_says(docker, "2026-11-01 03:05:00")
    assert tool.cmd_apply(by_hand(spansh, tmp_path / "galaxy", FILE), "check", None) == 0
    assert applied(docker) == ["/data/" + FILE]


def test_spansh_not_saying_when_the_file_was_made_stops_the_run(spansh, docker, tmp_path):
    spansh.publish(WEEK, b"seven days", "")
    chosen = choosing(spansh, tmp_path / "galaxy")
    database_says(docker, "2026-11-11 03:19:43")
    with pytest.raises(tool.UpdateError, match="did not say when its 7-day file was made"):
        tool.cmd_apply(chosen, "run", None)
    assert spansh.downloads() == [] and docker.calls("run") == []


def test_nothing_new_is_judged_from_the_database_and_scoring_still_runs(spansh, docker, tmp_path, capsys):
    """The 16:30 run, or the first run on another machine after a copy: the
    database already holds tonight's file, whatever is on this disk."""
    spansh.publish(WEEK, b"seven days", TONIGHT)
    chosen = choosing(spansh, tmp_path / "galaxy", EDFINDER_GALAXY_SCORES="on")
    database_says(docker, "2026-11-12 03:05:00")

    assert tool.cmd_apply(chosen, "run", None) == 0

    assert spansh.downloads() == [] and modules(docker) == [("spansh_delta.scores", "apply")]
    assert "Spansh has published nothing new since the last update." in capsys.readouterr().out
    # A check still checks, against tonight's file.
    assert tool.cmd_apply(chosen, "check", None) == 0
    assert applied(docker) == ["/data/" + WEEK] and modules(docker)[-1] == ("spansh_delta.apply", "check")


def test_the_same_file_twice_is_nothing_new_even_if_the_database_dates_it_earlier(
        spansh, docker, tmp_path, capsys):
    """The database dates a run by the first time its exact content was
    applied. If Spansh ever offered the same bytes again later, the record on
    this disk still knows it has been applied."""
    spansh.publish(WEEK, b"seven days", TONIGHT)
    chosen = choosing(spansh, tmp_path / "galaxy")
    database_says(docker, "2026-11-11 03:19:43")
    assert tool.cmd_apply(chosen, "run", None) == 0
    capsys.readouterr()
    assert tool.cmd_apply(chosen, "run", None) == 0  # the database still says 11 November
    assert applied(docker) == ["/data/" + WEEK] and spansh.downloads() == [WEEK]
    assert "nothing new" in capsys.readouterr().out


def test_after_a_run_only_the_file_just_applied_is_kept_and_check_scores_uses_it(
        spansh, docker, tmp_path):
    directory = tmp_path / "galaxy"
    # Left by the one-day launcher: its file, and the record of applying it.
    old = settings(spansh, directory)
    spansh.publish(FILE, b"one day", MONDAY)
    assert tool.cmd_apply(old, "run", None) == 0
    assert old.path(FILE).is_file()

    spansh.publish(WEEK, b"seven days", TONIGHT)
    chosen = choosing(spansh, directory)
    # Until the first new run, check-scores goes on using the file last applied.
    assert tool.cmd_apply(chosen, "check-scores", None) == 0
    assert docker.calls("run")[-1][-1] == "/data/" + FILE

    database_says(docker, "2026-11-11 03:19:43")
    assert tool.cmd_apply(chosen, "run", None) == 0
    assert sorted(path.name for path in directory.iterdir()) == [
        "galaxy_7days.json.gz", "galaxy_7days.json.gz.version", "last-applied.json", "lock"]

    assert tool.cmd_apply(chosen, "check-scores", None) == 0
    assert docker.calls("run")[-1][-1] == "/data/" + WEEK
    assert spansh.downloads() == [FILE, WEEK]


def test_a_failed_update_keeps_the_old_file_and_is_tried_again(spansh, docker, tmp_path):
    directory = tmp_path / "galaxy"
    spansh.publish(FILE, b"one day", MONDAY)
    assert tool.cmd_apply(settings(spansh, directory), "run", None) == 0
    spansh.publish(WEEK, b"seven days", TONIGHT)
    chosen = choosing(spansh, directory)
    database_says(docker, "2026-11-11 03:19:43")
    docker.state["module_exit"] = {"spansh_delta.apply": 1}
    docker.save()

    assert tool.cmd_apply(chosen, "run", None) == 1
    assert chosen.path(FILE).is_file()
    assert tool.fetch.read_record(chosen.record)["applied"]["modified"] == MONDAY

    docker.state["module_exit"] = {}
    docker.save()
    assert tool.cmd_apply(chosen, "run", None) == 0
    assert applied(docker)[-2:] == ["/data/" + WEEK, "/data/" + WEEK]
    assert spansh.downloads() == [FILE, WEEK]  # not downloaded twice


def test_check_scores_with_no_file_here_fetches_the_7_day_file_without_asking_the_database(
        spansh, docker, tmp_path):
    spansh.publish(WEEK, b"seven days", TONIGHT)
    chosen = choosing(spansh, tmp_path / "galaxy")
    database_says(docker, None)
    assert tool.cmd_apply(chosen, "check-scores", None) == 0
    assert spansh.downloads() == [WEEK] and not chosen.record.exists()
    assert docker.calls("run")[-1][-1] == "/data/" + WEEK


def test_status_says_how_the_file_is_chosen(spansh, docker, tmp_path, capsys):
    chosen = choosing(spansh, tmp_path / "galaxy")
    docker.state["psql"] = {"acquisition_kind = 'DELTA'": "", "rescore_queue": "0"}
    docker.save()
    assert tool.cmd_status(chosen) == 0
    out = capsys.readouterr().out
    assert ("Each run takes Spansh's 1-day file the night after the last update, its 7-day "
            "file every sixth night or after a missed night, and its 32-day file when the last "
            "update is more than 6 days old.") in out
    assert "No Spansh file on disk yet." in out


def test_bad_source_folder_is_refused():
    for value in ("ftp://example.test/", "https://example.test/files"):
        with pytest.raises(tool.UpdateError, match="EDFINDER_GALAXY_SOURCE"):
            tool.Settings(environ={"EDFINDER_GALAXY_SOURCE": value}, conf={})


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
    assert "Scoring after each update is switched off (EDFINDER_GALAXY_SCORES in deploy.conf)." in out
    assert "galaxy_1day.json.gz: 0.0 GB, published " + MONDAY in out
    assert "Each run takes %s/%s (EDFINDER_GALAXY_URL)." % (spansh.origin, FILE) in out
    assert tool.cmd_status(settings(spansh, tmp_path / "galaxy", EDFINDER_GALAXY_SCORES="on")) == 0
    assert "Scoring after each update is switched on." in capsys.readouterr().out


def test_status_before_anything_has_happened(spansh, docker, tmp_path, capsys):
    docker.state["psql"] = {"acquisition_kind = 'DELTA'": "", "rescore_queue": None, "reach_log": None}
    docker.save()
    assert tool.cmd_status(settings(spansh, tmp_path / "galaxy")) == 0
    out = capsys.readouterr().out
    assert "No galaxy update has been recorded yet." in out
    assert "does not exist yet (migration 026)" in out
    assert "Reach is not kept in this database (migration 028 has not been applied)." in out
    assert "No Spansh file on disk yet." in out


def test_status_says_when_reach_was_worked_out(spansh, docker, tmp_path, capsys):
    chosen = settings(spansh, tmp_path / "galaxy")
    docker.state["psql"] = {"acquisition_kind = 'DELTA'": "", "rescore_queue": "0", "reach_log": ""}
    docker.save()
    assert tool.cmd_status(chosen) == 0
    assert ("Reach has not been worked out yet. The scoring will not run until it has: "
            "python3 galaxy_update.py reach") in capsys.readouterr().out
    docker.state["psql"]["reach_log"] = "2026-11-13 04:41|157009|39176623|15102345"
    docker.save()
    assert tool.cmd_status(chosen) == 0
    assert ("Reach last worked out afresh 2026-11-13 04:41 UTC, from 157,009 inhabited systems: "
            "39,176,623 systems within 500 ly, 15,102,345 of them within 250 ly.") in capsys.readouterr().out


def test_status_says_how_far_finds_facts_have_got(spansh, docker, tmp_path, capsys):
    chosen = settings(spansh, tmp_path / "galaxy")
    docker.state["psql"] = {"acquisition_kind = 'DELTA'": "", "rescore_queue": "0", "economy_facts_fill": None}
    docker.save()
    assert tool.cmd_status(chosen) == 0
    assert ("Find's facts for each system are not kept in this database (migration 031 has not "
            "been applied).") in capsys.readouterr().out
    docker.state["psql"]["economy_facts_fill"] = ""
    docker.save()
    assert tool.cmd_status(chosen) == 0
    assert ("Find's facts for each system: no fill has started yet. The next scoring run starts it."
            in capsys.readouterr().out)
    docker.state["psql"]["economy_facts_fill"] = (
        "cre-knowledge-v0.5.0+edfinder.1/facts-1|2026-11-24 04:41||3500000|40912345")
    docker.save()
    assert tool.cmd_status(chosen) == 0
    assert ("Find's facts for each system: the fill for cre-knowledge-v0.5.0+edfinder.1/facts-1 started "
            "2026-11-24 04:41 UTC, 3,500,000 of 40,912,345 systems within 500 ly done so far; it carries "
            "on after each run.") in capsys.readouterr().out
    docker.state["psql"]["economy_facts_fill"] = (
        "cre-knowledge-v0.5.0+edfinder.1/facts-1|2026-11-24 04:41|2026-11-30 18:02|40912345|40912345")
    docker.save()
    assert tool.cmd_status(chosen) == 0
    assert ("the fill for cre-knowledge-v0.5.0+edfinder.1/facts-1 started 2026-11-24 04:41 UTC, finished "
            "2026-11-30 18:02 UTC.") in capsys.readouterr().out


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
