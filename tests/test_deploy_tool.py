"""The deploy tool's own logic, without Docker."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "edfinder_deploy", ROOT / "deploy" / "edfinder_deploy.py")
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

SHA = "a" * 40


def settings(**environ) -> "tool.Settings":
    return tool.Settings(environ=environ, conf={})


# -- settings -------------------------------------------------------------------

def test_defaults_match_the_current_server():
    value = settings()
    assert value.network == "edfinder-v3-production"
    assert value.api_env_file == "/etc/ed-finder/v3-production/api.env"
    assert value.live_bind == "127.0.0.1:58080" and value.trial_bind == "127.0.0.1:58081"
    assert value.project == "edfinder" and value.trial_project == "edfinder-trial"
    assert value.public_origin == "https://ed-finder.app"


def test_environment_wins_over_the_conf_file_and_the_conf_file_over_defaults():
    value = tool.Settings(environ={"EDFINDER_NETWORK": "from-env"},
                          conf={"EDFINDER_NETWORK": "from-file", "EDFINDER_PROJECT": "pi"})
    assert value.network == "from-env" and value.project == "pi"


def test_conf_file_is_key_value_lines_with_comments(tmp_path):
    conf = tmp_path / "deploy.conf"
    conf.write_text('# for the Pi\nEDFINDER_NETWORK = "home"\n\nnot a setting\nEDFINDER_PROJECT=pi\n')
    assert tool.read_conf(conf) == {"EDFINDER_NETWORK": "home", "EDFINDER_PROJECT": "pi"}
    assert tool.read_conf(tmp_path / "missing.conf") == {}


def test_public_check_can_be_switched_off():
    assert settings(EDFINDER_PUBLIC_ORIGIN="").public_origin == ""


def test_the_visitors_address_comes_from_x_real_ip_unless_the_machine_says_cloudflare():
    assert settings().client_address_from == "x_real_ip"
    pi = settings(EDFINDER_CLIENT_ADDRESS_FROM="cf_connecting_ip")
    assert pi.client_address_from == "cf_connecting_ip"
    env = tool.compose_env(pi, "edfinder", "api-image", "web-image", pi.live_bind, "unless-stopped")
    assert env["EDFINDER_CLIENT_ADDRESS_FROM"] == "cf_connecting_ip"
    # Only the two known headers: anything else could change the web server's settings.
    for wrong in ("X-Real-IP", "cf_connecting_ip; return 200", "remote_addr"):
        with pytest.raises(tool.DeployError):
            settings(EDFINDER_CLIENT_ADDRESS_FROM=wrong)


@pytest.mark.parametrize("environ", [
    {"EDFINDER_LIVE_BIND": "58080"}, {"EDFINDER_LIVE_BIND": "127.0.0.1:x"},
    {"EDFINDER_LIVE_BIND": "127.0.0.1:0"}, {"EDFINDER_TRIAL_BIND": "127.0.0.1:58080"},
    {"EDFINDER_PROJECT": "Bad Name"}, {"EDFINDER_READY_SECONDS": "soon"},
])
def test_bad_settings_are_refused_with_a_plain_message(environ):
    with pytest.raises(tool.DeployError):
        settings(**environ)


def test_bind_is_split_and_turned_into_an_address():
    assert tool.split_bind("127.0.0.1:58080") == ("127.0.0.1", 58080)
    assert tool.origin_of("127.0.0.1:58081") == "http://127.0.0.1:58081"


# -- reading container descriptions ---------------------------------------------------

def container(**overrides) -> dict:
    value = {
        "Name": "/old-web",
        "Config": {"Image": "img", "Env": ["PATH=/bin", "EDFINDER_API_UPSTREAM=old-api:8000"]},
        "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
        "NetworkSettings": {"Ports": {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "58080"}],
                                      "80/tcp": None}},
    }
    value.update(overrides)
    return value


def test_container_serving_an_address_is_recognised():
    assert tool.publishes(container(), "127.0.0.1:58080")
    assert not tool.publishes(container(), "127.0.0.1:58081")
    assert not tool.publishes(container(), "10.0.0.5:58080")
    everywhere = container(NetworkSettings={"Ports": {"8080/tcp": [
        {"HostIp": "0.0.0.0", "HostPort": "58080"}]}})
    assert tool.publishes(everywhere, "127.0.0.1:58080")
    assert not tool.publishes(container(NetworkSettings={}), "127.0.0.1:58080")


def test_api_partner_restart_policy_and_name_are_read():
    assert tool.container_name(container()) == "old-web"
    assert tool.upstream_partner(container()) == "old-api"
    assert tool.upstream_partner(container(Config={"Env": ["PATH=/bin"]})) is None
    assert tool.restart_policy(container()) == "unless-stopped"
    assert tool.restart_policy(container(HostConfig={})) == "no"
    assert tool.restart_policy(container(HostConfig={"RestartPolicy": {"Name": "weird"}})) == "no"


def test_only_setting_names_are_taken_from_an_env_file(tmp_path):
    path = tmp_path / "api.env"
    path.write_text("# comment\nDATABASE_URL=postgresql://u:secret@h/db\n\nexport TOKEN = abc\nFLAG\n")
    assert tool.env_file_names(str(path)) == {"DATABASE_URL", "TOKEN", "FLAG"}
    assert tool.setting_names(["A=1", "B=x=y", "C"]) == {"A", "B", "C"}


# -- health and smoke checks against a tiny local server -----------------------------

RANKINGS = "/api/archetypes/rankings?archetype=flexible&min_score=60&limit=5&offset=0&reach_ly=250"


class FakeSite:
    def __init__(self, **overrides) -> None:
        self.answers = {
            "/": (200, '<html><head><meta name="edfinder-build-sha" content="%s"></head></html>' % SHA),
            "/api/health": (200, json.dumps({"status": "ok", "database": "connected",
                                             "build_sha": SHA})),
            "/openapi.json": (200, json.dumps({"paths": {"/api/health": {},
                                                         "/api/v1/auth/session": {}}})),
            "/api/v1/auth/session": (200, json.dumps({"authenticated": False, "user": None})),
            RANKINGS: (200, json.dumps({"results": [{"name": "A", "claim_status": "free"}]})),
            "/api/local/search": (200, json.dumps(
                {"results": [{"name": "B", "claim_status": "claim_ended_unknown"}]})),
        }
        self.answers.update(overrides)
        self.posted: list = []
        answers, posted = self.answers, self.posted

        class Handler(BaseHTTPRequestHandler):
            def answer(self):
                status, body = answers.get(self.path, (404, "missing"))
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                self.answer()

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                posted.append((self.path, self.headers.get("Content-Type"),
                               json.loads(self.rfile.read(length))))
                self.answer()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.origin = "http://127.0.0.1:%d" % self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_site():
    sites = []

    def make(**overrides) -> FakeSite:
        site = FakeSite(**overrides)
        sites.append(site)
        return site

    yield make
    for site in sites:
        site.close()


def test_healthy_release_passes_every_check(fake_site):
    site = fake_site()
    tool.wait_ready(site.origin, SHA, 5)
    tool.smoke(site.origin, SHA)


def test_find_check_runs_three_real_searches(fake_site):
    site = fake_site()
    tool.find_check(site.origin)
    assert [(path, kind) for path, kind, _ in site.posted] == [
        ("/api/local/search", "application/json")] * 2
    galaxy, nearest = (body for _, _, body in site.posted)
    assert galaxy == {"galaxy_wide": True, "size": 5, "from": 0, "reach_ly": 250}
    assert nearest["sort_by"] == "distance"
    assert nearest["reference_coords"] == {"x": 0.0, "y": 0.0, "z": 0.0}
    assert nearest["reach_ly"] == 250


def test_find_check_accepts_a_build_from_before_the_claim_rule(fake_site):
    """Rolling back to an older build must still pass: its results carry no
    claim status at all."""
    old = (200, json.dumps({"results": [{"name": "Old"}]}))
    tool.find_check(fake_site(**{RANKINGS: old, "/api/local/search": old}).origin)


def results(*rows) -> tuple:
    return 200, json.dumps({"results": list(rows)})


@pytest.mark.parametrize("overrides, message", [
    ({RANKINGS: (503, "{}")}, "ranking for one colony type answered 503"),
    ({"/api/local/search": (503, "{}")}, "galaxy-wide search answered 503"),
    ({RANKINGS: (200, "not json")}, "did not answer with JSON"),
    ({RANKINGS: (200, json.dumps({"rows": []}))}, "without a list of results"),
    ({RANKINGS: results()}, "found no systems"),
    ({RANKINGS: results({"name": "Sol", "claim_status": "inhabited"})},
     "listed Sol, which is inhabited"),
    ({"/api/local/search": results({"name": "Depot", "claim_status": "being_claimed"})},
     "listed Depot, which is being claimed"),
])
def test_find_check_fails_when_find_is_broken_or_lists_a_hidden_system(
        fake_site, overrides, message):
    with pytest.raises(tool.DeployError, match=message):
        tool.find_check(fake_site(**overrides).origin)


# -- warming Find up -------------------------------------------------------------------

def ranking(colony_type: str, reach: int) -> str:
    return ("/api/archetypes/rankings?archetype=%s&min_score=60&limit=50&offset=0&reach_ly=%d"
            % (colony_type, reach))


def warm_site(fake_site, **overrides) -> FakeSite:
    """A site that answers every search warm asks, and nothing else."""
    answers = {ranking(kind, reach): (200, json.dumps(
        {"results": [], "total": 1000, "is_truncated": True}))
        for kind in tool.COLONY_TYPES for reach in (250, 500)}
    answers["/api/local/search"] = (200, json.dumps(
        {"results": [], "total": 412, "total_is_capped": False}))
    answers.update(overrides)
    return fake_site(**answers)


def warm_settings(site: FakeSite, **environ) -> "tool.Settings":
    return settings(EDFINDER_LIVE_BIND=site.origin.removeprefix("http://"), **environ)


def test_warm_runs_the_searches_visitors_start_with_on_the_live_address(fake_site, capsys):
    site = warm_site(fake_site)
    tool.command_warm(warm_settings(site))
    printed = capsys.readouterr().out.splitlines()
    # Galaxy-wide, Any and each colony type, at both reaches: 18 searches.
    assert len(printed) == 19 and printed[-1] == "OK: Find is warmed up. Nothing was changed."
    assert printed[0].startswith("Any, galaxy-wide, 250 ly") and printed[0].endswith("412 found")
    assert printed[1].startswith("paradise, galaxy-wide, 250 ly")
    assert printed[1].endswith("1,000+ found")
    assert printed[9].startswith("Any, galaxy-wide, 500 ly")
    assert all(re.search(r" \d+ ms  ", line) for line in printed[:-1])
    # Any as the web app asks for it, at each reach, and waiting for its
    # count: Find otherwise answers the page without a slow count, and what
    # the count reads would stay cold.
    assert [body for _, _, body in site.posted] == [
        {"galaxy_wide": True, "sort_by": "development", "size": 50, "from": 0, "reach_ly": reach,
         "wait_for_count": True}
        for reach in (250, 500)]


def test_warm_says_so_when_a_search_answered_without_its_count(fake_site, capsys):
    site = warm_site(fake_site, **{"/api/local/search": (200, json.dumps(
        {"results": [], "total": 50, "total_is_capped": False, "total_is_counted": False}))})
    tool.command_warm(warm_settings(site))
    printed = capsys.readouterr().out.splitlines()
    # Not "50 found": 50 is how many rows the page showed, not a count.
    assert printed[0].endswith("answered, but its matches were not counted in time")
    assert "found" not in printed[0]
    assert printed[-1] == "OK: Find is warmed up. Nothing was changed."


def test_warm_asks_for_the_apis_own_colony_types_tier_and_reaches():
    from edfinder_api.ranking.profile import ARCHETYPE_KEYS, ARCHETYPE_SCORE_FLOOR_DEFAULT
    from edfinder_api.reach_rule import REACH_CHOICES

    assert tool.COLONY_TYPES == ARCHETYPE_KEYS
    # The web app's default minimum tier, B, is a fit of 60 or more.
    assert tool.WARM_MIN_SCORE == ARCHETYPE_SCORE_FLOOR_DEFAULT == 60
    assert tool.WARM_REACHES == REACH_CHOICES


def test_warm_runs_every_search_and_then_fails_if_any_did(fake_site, capsys):
    site = warm_site(fake_site, **{
        ranking("stronghold", 500): (503, "{}"),
        ranking("flexible", 250): (200, "not json")})
    with pytest.raises(tool.DeployError, match="2 of 18 searches failed"):
        tool.command_warm(warm_settings(site))
    printed = capsys.readouterr().out.splitlines()
    assert len(printed) == 18
    assert [line.split("  ")[0] for line in printed if "FAILED" in line] == [
        "flexible, galaxy-wide, 250 ly", "stronghold, galaxy-wide, 500 ly"]
    assert "FAILED: did not answer with JSON" in printed[8]
    assert "FAILED: answered 503" in printed[15]


def test_warm_says_so_when_the_site_does_not_answer(capsys):
    with socket.socket() as spare:
        spare.bind(("127.0.0.1", 0))
        port = spare.getsockname()[1]
    with pytest.raises(tool.DeployError, match="18 of 18 searches failed"):
        tool.command_warm(settings(EDFINDER_LIVE_BIND="127.0.0.1:%d" % port))
    assert "FAILED: no answer from http://127.0.0.1:%d" % port in capsys.readouterr().out


def test_warm_does_nothing_on_a_machine_without_find_data(capsys):
    tool.command_warm(settings(EDFINDER_FIND_CHECK="0", EDFINDER_LIVE_BIND="127.0.0.1:9"))
    assert capsys.readouterr().out == (
        "Find has no data on this machine (EDFINDER_FIND_CHECK=0): nothing to warm up.\n")


def test_warm_is_a_command_that_needs_no_lock(fake_site, monkeypatch, capsys):
    site = warm_site(fake_site)
    monkeypatch.setenv("EDFINDER_LIVE_BIND", site.origin.removeprefix("http://"))
    monkeypatch.setenv("EDFINDER_FIND_CHECK", "1")

    def no_lock(directory):
        raise AssertionError("warm must not take the deploy lock")

    monkeypatch.setattr(tool, "single_run", no_lock)
    assert tool.main(["warm"]) == 0
    assert capsys.readouterr().out.endswith("OK: Find is warmed up. Nothing was changed.\n")


# -- older images ---------------------------------------------------------------

API, WEB = "ghcr.io/example/site/api", "ghcr.io/example/site/web"


class FakeDocker:
    """Docker's list of images, as far as the tidy-up after going live uses it:
    each name and the image it is a name for."""

    def __init__(self, images: dict[str, str]) -> None:
        self.images = dict(images)
        self.in_use: set[str] = set()
        self.unreadable: set[str] = set()
        self.asked: list[tuple[str, ...]] = []

    def docker(self, *args: str, check: bool = True, timeout: float = 900) -> str:
        self.asked.append(args)
        if args[:2] == ("image", "inspect"):
            return "" if args[-1] in self.unreadable else self.images.get(args[-1], "")
        if args[:2] == ("image", "ls"):
            return "\n".join("%s %s" % (name, identity) for name, identity in self.images.items()
                             if name.rsplit(":", 1)[0] == args[-1])
        raise AssertionError("not expected of the tidy-up: docker %s" % " ".join(args))

    def run(self, command, *, check: bool = True, **_) -> subprocess.CompletedProcess:
        assert command[:3] == ["docker", "image", "rm"] and len(command) == 4, command
        self.asked.append(tuple(command[1:]))
        if command[3] in self.in_use:
            return subprocess.CompletedProcess(command, 1, "", "image is being used by a container")
        del self.images[command[3]]
        return subprocess.CompletedProcess(command, 0, "", "")


def release(letter: str) -> "tool.Release":
    sha = letter * 40
    return tool.Release("git-" + sha, sha, "%s:git-%s" % (API, sha), "%s:git-%s" % (WEB, sha))


def images_of(*letters: str) -> dict[str, str]:
    return {"%s:git-%s" % (repository, letter * 40): "sha256:%s-%s" % (role, letter)
            for letter in letters for role, repository in (("api", API), ("web", WEB))}


@pytest.fixture
def fake_docker(monkeypatch):
    def install(images: dict[str, str]) -> FakeDocker:
        fake = FakeDocker(images)
        monkeypatch.setattr(tool, "docker", fake.docker)
        monkeypatch.setattr(tool, "run", fake.run)
        return fake
    return install


def tidy_settings(**environ) -> "tool.Settings":
    return settings(EDFINDER_API_REPOSITORY=API, EDFINDER_WEB_REPOSITORY=WEB, **environ)


def test_going_live_removes_images_that_neither_the_live_build_nor_the_one_before_uses(
        fake_docker, tmp_path, capsys):
    fake = fake_docker({
        **images_of("a", "b", "c", "d"),
        # A second name for the live build's API image: the tag it was fetched by.
        API + ":main": "sha256:api-d",
        # Not the site's: the database, and the listener.
        "postgres:18.6-trixie": "sha256:postgres",
        "ghcr.io/example/site/eddn:main": "sha256:eddn",
    })
    state = tool.State(tmp_path)
    state.current, state.previous = release("d"), release("c")

    tool.remove_old_images(tidy_settings(), state)

    assert fake.images == {**images_of("c", "d"), API + ":main": "sha256:api-d",
                           "postgres:18.6-trixie": "sha256:postgres",
                           "ghcr.io/example/site/eddn:main": "sha256:eddn"}
    said = capsys.readouterr().out
    assert "Removed 4 older images of the site from this machine." in said
    assert "REMOVED 4 older images" in (tmp_path / "deploy.log").read_text()


def test_with_nothing_older_on_the_machine_nothing_is_removed_or_said(fake_docker, tmp_path, capsys):
    fake = fake_docker(images_of("c", "d"))
    state = tool.State(tmp_path)
    state.current, state.previous = release("d"), release("c")
    tool.remove_old_images(tidy_settings(), state)
    assert fake.images == images_of("c", "d")
    assert capsys.readouterr().out == ""


def test_the_first_release_keeps_its_own_images_only(fake_docker, tmp_path):
    fake = fake_docker(images_of("a", "d"))
    state = tool.State(tmp_path)
    state.current = release("d")
    tool.remove_old_images(tidy_settings(), state)
    assert fake.images == images_of("d")


def test_no_image_is_removed_unless_every_image_to_keep_can_be_read(fake_docker, tmp_path, capsys):
    """If Docker cannot say what the build before is, comparing against the
    rest would remove it. So nothing is removed at all."""
    fake = fake_docker(images_of("a", "c", "d"))
    fake.unreadable.add("%s:git-%s" % (WEB, "c" * 40))
    state = tool.State(tmp_path)
    state.current, state.previous = release("d"), release("c")
    tool.remove_old_images(tidy_settings(), state)
    assert fake.images == images_of("a", "c", "d")
    assert "Older images were left alone" in capsys.readouterr().out


def test_an_image_a_container_still_uses_is_left_and_said(fake_docker, tmp_path, capsys):
    fake = fake_docker(images_of("a", "b", "d"))
    fake.in_use.add("%s:git-%s" % (API, "a" * 40))
    state = tool.State(tmp_path)
    state.current = release("d")
    tool.remove_old_images(tidy_settings(), state)
    assert set(fake.images) == set(images_of("d")) | {"%s:git-%s" % (API, "a" * 40)}
    said = capsys.readouterr().out
    assert "Removed 3 older images of the site" in said
    assert "1 older image is still in use and left as it is." in said


def test_the_tidy_up_cannot_fail_a_deploy_that_is_already_live(monkeypatch, tmp_path, capsys):
    def docker_is_stuck(*args, **kwargs):
        raise tool.DeployError("timed out after 900 s: docker image inspect")
    monkeypatch.setattr(tool, "docker", docker_is_stuck)
    state = tool.State(tmp_path)
    state.current = release("d")
    tool.remove_old_images(tidy_settings(), state)
    assert "Older images were left alone: timed out after 900 s" in capsys.readouterr().out


def test_old_images_can_be_kept_and_only_0_or_1_says_so(fake_docker, tmp_path):
    assert settings().keep_old_images is False
    fake = fake_docker(images_of("a", "d"))
    state = tool.State(tmp_path)
    state.current = release("d")
    tool.remove_old_images(tidy_settings(EDFINDER_KEEP_OLD_IMAGES="1"), state)
    assert fake.images == images_of("a", "d") and fake.asked == []
    with pytest.raises(tool.DeployError, match="EDFINDER_KEEP_OLD_IMAGES must be 0 or 1"):
        settings(EDFINDER_KEEP_OLD_IMAGES="yes")


def unit_lines(name: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for line in (ROOT / "deploy" / name).read_text(encoding="utf-8").splitlines():
        if line and not line.startswith(("#", "[")):
            key, _, value = line.partition("=")
            found.setdefault(key, []).append(value)
    return found


def test_the_warm_up_follows_the_jobs_that_read_the_most():
    warm = unit_lines("edfinder-find-warmup.service")
    assert warm["Type"] == ["oneshot"]
    assert warm["ExecStart"] == ["/usr/bin/python3 /opt/edfinder/edfinder_deploy.py warm"]
    # Started when the galaxy update or the full backup has ended well...
    for job in ("edfinder-galaxy-update.service", "edfinder-backup-full.service"):
        assert unit_lines(job)["OnSuccess"] == ["edfinder-find-warmup.service"]
    # ...but not by the state refresh: the galaxy update follows it each night.
    assert "OnSuccess" not in unit_lines("edfinder-state-refresh.service")
    # It waits for any nightly job still running, and is never on a timer of its own.
    assert set(warm["After"][0].split()) >= {
        "edfinder-state-refresh.service", "edfinder-galaxy-update.service",
        "edfinder-backup-full.service"}
    assert not (ROOT / "deploy" / "edfinder-find-warmup.timer").exists()


def test_find_check_can_be_switched_off_only_with_0_or_1():
    assert settings().find_check is True
    assert settings(EDFINDER_FIND_CHECK="0").find_check is False
    with pytest.raises(tool.DeployError, match="EDFINDER_FIND_CHECK must be 0 or 1"):
        settings(EDFINDER_FIND_CHECK="no")


def health(**changes) -> tuple:
    value = {"status": "ok", "database": "connected", "build_sha": SHA}
    value.update(changes)
    return 200, json.dumps(value)


@pytest.mark.parametrize("overrides, message", [
    ({"/api/health": health(database="disconnected")}, "database is 'disconnected'"),
    ({"/api/health": health(status="degraded")}, "status is 'degraded'"),
    ({"/api/health": health(build_sha="b" * 40)}, "it reports build bbbbbbbbbbbb"),
    ({"/api/health": (503, "down")}, "/api/health answered 503"),
    ({"/api/health": (200, "not json")}, "the API did not answer with JSON"),
    ({"/": (200, "<html><head></head></html>")}, "the web page is not build"),
    ({"/": (500, "oops")}, "/ answered 500"),
    ({"/openapi.json": (200, json.dumps({"paths": {}}))}, "missing expected routes"),
    ({"/api/v1/auth/session": (200, json.dumps({"authenticated": True, "user": {"id": 1}}))},
     "was not reported as signed out"),
])
def test_each_kind_of_broken_release_fails_the_checks(fake_site, overrides, message):
    site = fake_site(**overrides)
    with pytest.raises(tool.DeployError, match=message):
        tool.smoke(site.origin, SHA)


def test_waiting_gives_up_with_the_reason(fake_site):
    site = fake_site(**{"/api/health": health(database="disconnected")})
    with pytest.raises(tool.DeployError, match="not healthy after 1 s: database is 'disconnected'"):
        tool.wait_ready(site.origin, SHA, 1)
    with pytest.raises(tool.DeployError, match="not healthy after 1 s"):
        tool.wait_ready("http://127.0.0.1:9", SHA, 1)


def test_waiting_for_any_healthy_build_ignores_which_build_it_is(fake_site):
    site = fake_site(**{"/api/health": health(build_sha="f" * 40)})
    tool.wait_ready(site.origin, None, 5)


# -- state ----------------------------------------------------------------------

def test_state_survives_being_saved_and_read_back(tmp_path):
    state = tool.State(tmp_path / "state")
    assert state.current is None and state.previous is None and state.original_containers == []
    state.current = tool.Release("git-" + SHA, SHA, "repo/api:git-" + SHA, "repo/web:git-" + SHA,
                                 "2026-11-09T23:00:00Z")
    state.original_containers = [{"name": "old-api", "restart": "unless-stopped"}]
    state.save()
    state.log("LIVE something")
    again = tool.State(tmp_path / "state")
    assert again.current.sha == SHA and again.current.api == "repo/api:git-" + SHA
    assert again.original_containers == [{"name": "old-api", "restart": "unless-stopped"}]
    assert "LIVE something" in (tmp_path / "state" / "deploy.log").read_text()


def test_damaged_state_file_is_reported_not_guessed_at(tmp_path):
    (tmp_path / "state.json").write_text("{not json")
    with pytest.raises(tool.DeployError, match="is damaged"):
        tool.State(tmp_path)


@pytest.mark.parametrize("value", [None, "text", {}, {"tag": "x", "sha": "short", "api": "a", "web": "w"}])
def test_release_records_that_make_no_sense_are_ignored(value):
    assert tool.Release.from_json(value) is None


def test_only_one_deploy_can_hold_the_lock(tmp_path):
    with tool.single_run(tmp_path):
        with pytest.raises(tool.Refused, match="another deploy is running"):
            with tool.single_run(tmp_path):
                pass
    with tool.single_run(tmp_path):
        pass


def test_without_root_the_lock_refusal_says_to_use_sudo(tmp_path, monkeypatch):
    """On the Pi, 13 Nov: "live" typed without sudo ended in a Python error
    trace about the lock file. It must say what to do instead."""
    def no_permission(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "mkdir", no_permission)
    monkeypatch.setattr(tool.os, "geteuid", lambda: 1000)
    with pytest.raises(tool.Refused) as refusal:
        with tool.single_run(tmp_path / "state"):
            pass
    assert "sudo in front of it" in str(refusal.value)
    assert str(tmp_path / "state") in str(refusal.value)

    # Root is not told to use sudo: for root the cause is something else.
    monkeypatch.setattr(tool.os, "geteuid", lambda: 0)
    with pytest.raises(tool.Refused) as refusal:
        with tool.single_run(tmp_path / "state"):
            pass
    assert "sudo" not in str(refusal.value)
    assert str(tmp_path / "state") in str(refusal.value)


def test_a_refused_permission_anywhere_is_said_in_plain_words(monkeypatch, capsys):
    def no_permission(settings):
        raise PermissionError(13, "Permission denied", "/var/lib/edfinder-deploy/state.json")

    monkeypatch.setattr(tool, "command_status", no_permission)
    monkeypatch.setattr(tool.os, "geteuid", lambda: 1000)

    assert tool.main(["status"]) == 1
    printed = capsys.readouterr().out
    assert printed.startswith("FAILED: the machine would not allow it")
    assert "/var/lib/edfinder-deploy/state.json" in printed
    assert "sudo in front of it" in printed
    assert "Traceback" not in printed


def test_programs_are_read_from_what_ss_prints():
    # Printed by `ss -H -ltnp 'sport = :58080'` (iproute2 5.15) with a Python
    # web server listening there, 13 Nov 2026.
    listing = 'LISTEN 0      5      127.0.0.1:58080 0.0.0.0:* users:(("python3",pid=4,fd=3))\n'
    assert tool.programs_in(listing) == ["python3 (process 4)"]
    # With nothing listening it prints nothing.
    assert tool.programs_in("") == []


def test_an_address_held_outside_docker_is_reported_with_its_program():
    """On the Pi, 13 Nov: "live" refused because the live address was in use
    "by a program outside Docker" and did not say which. This process listens
    on an address here, and the tool has to name it, or say how to find out
    where the machine has no `ss`."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        bind = "127.0.0.1:%d" % port

        assert tool.port_is_free(bind) is False
        report = tool.held_outside_docker(bind)

    assert report.startswith("in use by a program outside Docker")
    if shutil.which("ss"):
        assert "(process %d)" % os.getpid() in report
    else:
        assert "sudo ss -H -ltnp 'sport = :%d'" % port in report


def test_tags_that_are_not_image_tags_are_refused():
    for bad in ("", "has space", "semi;colon", "../etc", "a" * 200):
        with pytest.raises(tool.DeployError, match="not a valid image tag"):
            tool.resolve_release(settings(), bad, pull=False)


def test_the_stand_in_web_image_renders_the_web_servers_settings_as_the_real_one_does():
    """The Docker tests run the real nginx settings in a stand-in image; it must
    fill in the same settings, with the same default, or they test something else."""
    def settings_of(dockerfile: Path) -> tuple:
        text = dockerfile.read_text()
        found = tuple(re.search(r'%s="([^"]*)"' % name, text).group(1)
                      for name in ("NGINX_ENVSUBST_FILTER", "EDFINDER_CLIENT_ADDRESS_FROM"))
        return found

    real = settings_of(ROOT / "apps" / "web" / "Dockerfile")
    assert real == ("^EDFINDER_(API_UPSTREAM|CLIENT_ADDRESS_FROM)$", "x_real_ip")
    assert settings_of(ROOT / "tests" / "deploy_stubs" / "web.Dockerfile") == real
