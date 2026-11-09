"""The deploy tool's own logic, without Docker."""
from __future__ import annotations

import importlib.util
import json
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

class FakeSite:
    def __init__(self, **overrides) -> None:
        self.answers = {
            "/": (200, '<html><head><meta name="edfinder-build-sha" content="%s"></head></html>' % SHA),
            "/api/health": (200, json.dumps({"status": "ok", "database": "connected",
                                             "build_sha": SHA})),
            "/openapi.json": (200, json.dumps({"paths": {"/api/health": {},
                                                         "/api/v1/auth/session": {}}})),
            "/api/v1/auth/session": (200, json.dumps({"authenticated": False, "user": None})),
        }
        self.answers.update(overrides)
        answers = self.answers

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                status, body = answers.get(self.path, (404, "missing"))
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

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


def test_tags_that_are_not_image_tags_are_refused():
    for bad in ("", "has space", "semi;colon", "../etc", "a" * 200):
        with pytest.raises(tool.DeployError, match="not a valid image tag"):
            tool.resolve_release(settings(), bad, pull=False)
