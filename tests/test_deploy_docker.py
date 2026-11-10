"""The deploy tool, exercised against real Docker containers.

Stand-in API and web images (tests/deploy_stubs) play the app, so these tests
cover the tool's own behaviour: testing on the spare address, switching,
taking over from containers it did not start, rolling back and restoring.

Skipped when Docker is not available, unless REQUIRE_DOCKER_TESTS=1 is set
(CI sets it so they can never be skipped silently).
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.request
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "deploy" / "edfinder_deploy.py"
STUBS = ROOT / "tests" / "deploy_stubs"
REQUIRED = os.environ.get("REQUIRE_DOCKER_TESTS") == "1"

SHA_OLD = "0" * 40
SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_BAD = "c" * 40
SHA_FIND = "e" * 40


def _docker_reason() -> str:
    if shutil.which("docker") is None:
        return "docker is not installed"
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        return "the Docker daemon is not running"
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0:
        return "the Docker Compose plugin is missing"
    return ""


_REASON = _docker_reason()
if _REASON and REQUIRED:
    raise RuntimeError("Docker tests are required but cannot run: " + _REASON)
pytestmark = pytest.mark.skipif(bool(_REASON), reason=_REASON or "available")


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise AssertionError("docker %s failed:\n%s" % (" ".join(args[:3]), result.stderr))
    return result.stdout.strip()


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def base_image() -> str:
    """The web image's own web-server base, so nothing extra is downloaded."""
    froms = re.findall(r"^FROM (\S+) AS runtime", (ROOT / "apps/web/Dockerfile").read_text(),
                       flags=re.MULTILINE)
    assert froms, "runtime base image not found in apps/web/Dockerfile"
    return froms[-1]


class Images:
    def __init__(self, suffix: str) -> None:
        self.api = "local/edft-%s-api" % suffix
        self.web = "local/edft-%s-web" % suffix


@pytest.fixture(scope="module")
def images(tmp_path_factory) -> Images:
    suffix = uuid.uuid4().hex[:8]
    names = Images(suffix)
    base = base_image()
    context = tmp_path_factory.mktemp("stub-context")
    shutil.copy(STUBS / "api.conf.template", context / "api.conf.template")
    shutil.copy(ROOT / "apps/web/nginx/default.conf.template", context / "default.conf.template")
    shutil.copy(ROOT / "apps/web/nginx/proxy_params", context / "proxy_params")
    built = []

    def build(kind: str, sha: str, tag: str, database: str = "connected",
              find_answer: str = "") -> None:
        html = context / "html"
        shutil.rmtree(html, ignore_errors=True)
        html.mkdir()
        page = ('<html><head><meta name="edfinder-build-sha" content="%s"></head>'
                '<body>stand-in</body></html>' % sha)
        (html / "index.html").write_text(page)
        (html / "200.html").write_text(page)
        reference = "%s:%s" % (names.api if kind == "api" else names.web, tag)
        extra = ["--build-arg", "FIND_ANSWER=" + find_answer] if find_answer else []
        docker("build", "-q", "-f", str(STUBS / ("%s.Dockerfile" % kind)),
               "--build-arg", "BASE=" + base, "--build-arg", "BUILD_SHA=" + sha,
               "--build-arg", "DATABASE=" + database, *extra, "-t", reference, str(context))
        built.append(reference)

    for sha in (SHA_OLD, SHA_A, SHA_B):
        build("api", sha, "git-" + sha)
        build("web", sha, "git-" + sha)
    build("api", SHA_BAD, "git-" + SHA_BAD, database="disconnected")
    # Healthy, but its Find lists an inhabited system.
    build("api", SHA_FIND, "git-" + SHA_FIND,
          find_answer='{"results":[{"name":"Sol","claim_status":"inhabited"}]}')
    build("web", SHA_FIND, "git-" + SHA_FIND)
    build("web", SHA_BAD, "git-" + SHA_BAD)
    build("api", SHA_B, "main")
    build("web", SHA_B, "main")
    build("api", SHA_A, "mixed")
    build("web", SHA_B, "mixed")
    yield names
    for reference in built:
        docker("rmi", "-f", reference, check=False)


class Site:
    def __init__(self, images: Images, tmp_path: Path) -> None:
        self.images = images
        self.suffix = uuid.uuid4().hex[:8]
        self.project = "edft%s" % self.suffix
        self.network = "edft-net-%s" % self.suffix
        self.live_port, self.trial_port = free_port(), free_port()
        self.state_dir = tmp_path / "state"
        self.env_file = tmp_path / "api.env"
        self.env_file.write_text("STUB_SETTING=1\n")
        self.old_api = "edft-old-api-%s" % self.suffix
        self.old_web = "edft-old-web-%s" % self.suffix
        docker("network", "create", self.network)

    def tool(self, *args: str, ready_seconds: int = 30,
             environ: dict | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update({
            "EDFINDER_API_REPOSITORY": self.images.api,
            "EDFINDER_WEB_REPOSITORY": self.images.web,
            "EDFINDER_NETWORK": self.network,
            "EDFINDER_API_ENV_FILE": str(self.env_file),
            "EDFINDER_LIVE_BIND": "127.0.0.1:%d" % self.live_port,
            "EDFINDER_TRIAL_BIND": "127.0.0.1:%d" % self.trial_port,
            "EDFINDER_PROJECT": self.project,
            "EDFINDER_STATE_DIR": str(self.state_dir),
            "EDFINDER_READY_SECONDS": str(ready_seconds),
            "EDFINDER_PUBLIC_ORIGIN": "",
        })
        env.update(environ or {})
        return subprocess.run([sys.executable, str(TOOL), "--no-pull", *args],
                              env=env, capture_output=True, text=True, timeout=300)

    def serving(self, port: int | None = None) -> str | None:
        """The build the given address is serving, or None if nothing answers."""
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open("http://127.0.0.1:%d/api/health" % (port or self.live_port),
                             timeout=5) as response:
                return json.loads(response.read())["build_sha"]
        except OSError:
            return None

    def page_build(self) -> str | None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open("http://127.0.0.1:%d/" % self.live_port, timeout=5) as response:
                found = re.search(r'edfinder-build-sha" content="([0-9a-f]{40})"',
                                  response.read().decode())
                return found.group(1) if found else None
        except OSError:
            return None

    def state(self) -> dict:
        return json.loads((self.state_dir / "state.json").read_text())

    def names(self) -> set[str]:
        return set(docker("ps", "--format", "{{.Names}}").split())

    def restart_policy(self, name: str) -> str:
        return docker("inspect", "--format", "{{.HostConfig.RestartPolicy.Name}}", name)

    def start_old_site(self, *api_options: str) -> None:
        """A site this tool did not start, holding the live address."""
        docker("run", "-d", "--name", self.old_api, "--network", self.network,
               "--restart", "unless-stopped", *api_options,
               "%s:git-%s" % (self.images.api, SHA_OLD))
        docker("run", "-d", "--name", self.old_web, "--network", self.network,
               "--restart", "unless-stopped",
               "-e", "EDFINDER_API_UPSTREAM=%s:8000" % self.old_api,
               "-p", "127.0.0.1:%d:8080" % self.live_port,
               "%s:git-%s" % (self.images.web, SHA_OLD))
        for _ in range(60):
            if self.serving() == SHA_OLD:
                return
            subprocess.run(["sleep", "0.5"])
        raise AssertionError("the stand-in old site did not start")

    def cleanup(self) -> None:
        wanted = [self.old_api, self.old_web] + [
            "%s%s-%s" % (self.project, trial, role)
            for trial in ("", "-trial") for role in ("api", "web")]
        docker("rm", "-f", *wanted, check=False)
        docker("network", "rm", self.network, self.network + "-extra", check=False)


@pytest.fixture
def site(images, tmp_path) -> Site:
    site = Site(images, tmp_path)
    yield site
    site.cleanup()


def tag(sha: str) -> str:
    return "git-" + sha


# -- check and try --------------------------------------------------------------

def test_check_reports_a_ready_machine_and_changes_nothing(site):
    result = site.tool("check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK: this machine is ready. Nothing was changed." in result.stdout
    assert "contents not shown" in result.stdout
    assert "STUB_SETTING" not in result.stdout
    assert site.names().isdisjoint({site.project + "-api", site.project + "-web"})
    assert not (site.state_dir / "state.json").exists()


def test_check_names_a_missing_network(site):
    docker("network", "rm", site.network)
    result = site.tool("check")
    assert result.returncode == 1
    assert "Docker network %s does not exist" % site.network in result.stdout


def test_try_passes_and_leaves_nothing_running(site):
    result = site.tool("try", tag(SHA_A))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "The live site was not touched." in result.stdout
    assert site.serving(site.trial_port) is None
    assert site.serving() is None
    assert not any(name.startswith(site.project) for name in site.names())


def test_try_of_a_broken_release_fails_and_cleans_up(site):
    result = site.tool("try", tag(SHA_BAD), ready_seconds=4)
    assert result.returncode == 1
    assert "database is 'disconnected', not connected" in result.stdout
    assert not any(name.startswith(site.project) for name in site.names())


# -- going live -----------------------------------------------------------------

def test_first_release_goes_live_on_a_free_address(site):
    result = site.tool("live", tag(SHA_A))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "LIVE: the site is now running" in result.stdout
    assert site.serving() == SHA_A and site.page_build() == SHA_A
    assert site.restart_policy(site.project + "-api") == "unless-stopped"
    assert site.restart_policy(site.project + "-web") == "unless-stopped"
    state = site.state()
    assert state["current"]["sha"] == SHA_A and state["previous"] is None
    assert site.serving(site.trial_port) is None
    assert "LIVE git-" + SHA_A in (site.state_dir / "deploy.log").read_text()


def test_newer_release_replaces_the_live_one_and_rollback_returns(site):
    assert site.tool("live", tag(SHA_A)).returncode == 0
    result = site.tool("live", tag(SHA_B))
    assert result.returncode == 0, result.stdout + result.stderr
    assert site.serving() == SHA_B and site.page_build() == SHA_B
    assert (site.state()["current"]["sha"], site.state()["previous"]["sha"]) == (SHA_B, SHA_A)

    result = site.tool("rollback")
    assert result.returncode == 0, result.stdout + result.stderr
    assert site.serving() == SHA_A and site.page_build() == SHA_A
    assert (site.state()["current"]["sha"], site.state()["previous"]["sha"]) == (SHA_A, SHA_B)


def test_broken_release_is_caught_on_the_spare_address_and_the_site_is_untouched(site):
    assert site.tool("live", tag(SHA_A)).returncode == 0
    started = docker("inspect", "--format", "{{.State.StartedAt}}", site.project + "-web")
    result = site.tool("live", tag(SHA_BAD), ready_seconds=4)
    assert result.returncode == 1
    assert "the test start failed" in result.stdout
    assert site.serving() == SHA_A
    assert docker("inspect", "--format", "{{.State.StartedAt}}",
                  site.project + "-web") == started
    assert site.state()["current"]["sha"] == SHA_A


def test_release_whose_find_lists_an_inhabited_system_never_goes_live(site):
    assert site.tool("live", tag(SHA_A)).returncode == 0
    result = site.tool("live", tag(SHA_FIND))
    assert result.returncode == 1
    assert "listed Sol, which is inhabited" in result.stdout
    assert site.serving() == SHA_A and site.state()["current"]["sha"] == SHA_A
    # With the Find check switched off (a machine with no Finder data yet)
    # the same release is accepted.
    result = site.tool("live", tag(SHA_FIND), environ={"EDFINDER_FIND_CHECK": "0"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert site.serving() == SHA_FIND


def test_broken_release_that_skips_the_test_is_rolled_back(site):
    assert site.tool("live", tag(SHA_A)).returncode == 0
    result = site.tool("live", tag(SHA_BAD), "--skip-trial", ready_seconds=4)
    assert result.returncode == 1
    assert "did not go live" in result.stdout
    assert "is serving the site again" in result.stdout
    assert site.serving() == SHA_A and site.page_build() == SHA_A
    assert site.state()["current"]["sha"] == SHA_A
    assert "rolled back to git-" + SHA_A in (site.state_dir / "deploy.log").read_text()


def test_first_release_that_fails_with_nothing_to_return_to_says_so(site):
    result = site.tool("live", tag(SHA_BAD), "--skip-trial", ready_seconds=4)
    assert result.returncode == 1
    assert "There was no earlier release to go back to" in result.stdout
    assert not (site.state_dir / "state.json").exists() or site.state()["current"] is None


def test_moving_tag_is_pinned_to_the_build_it_held(site):
    result = site.tool("live", "main")
    assert result.returncode == 0, result.stdout + result.stderr
    state = site.state()
    assert state["current"]["tag"] == "git-" + SHA_B
    assert state["current"]["api"] == "%s:git-%s" % (site.images.api, SHA_B)
    assert docker("inspect", "--format", "{{.Config.Image}}",
                  site.project + "-api") == "%s:git-%s" % (site.images.api, SHA_B)


# -- taking over from containers the tool did not start ------------------------------

def test_site_started_by_something_else_is_not_replaced_without_being_asked(site):
    site.start_old_site()
    result = site.tool("live", tag(SHA_A))
    assert result.returncode == 2
    assert "REFUSED, nothing was changed" in result.stdout
    assert "--take-over" in result.stdout and site.old_web in result.stdout
    assert "Testing" not in result.stdout  # refused before any work was done
    assert site.serving() == SHA_OLD
    assert {site.old_api, site.old_web} <= site.names()


def test_take_over_keeps_the_old_containers_and_restore_old_brings_them_back(site):
    site.start_old_site()
    result = site.tool("live", tag(SHA_A), "--take-over")
    assert result.returncode == 0, result.stdout + result.stderr
    assert site.serving() == SHA_A and site.page_build() == SHA_A
    running = site.names()
    assert site.old_api not in running and site.old_web not in running
    # stopped, not deleted, and they will not start themselves again
    assert docker("inspect", "--format", "{{.State.Status}}", site.old_web) == "exited"
    assert docker("inspect", "--format", "{{.State.Status}}", site.old_api) == "exited"
    assert site.restart_policy(site.old_web) == "no" and site.restart_policy(site.old_api) == "no"
    assert site.state()["original_containers"] == [
        {"name": site.old_api, "restart": "unless-stopped"},
        {"name": site.old_web, "restart": "unless-stopped"}]

    result = site.tool("restore-old")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESTORED" in result.stdout
    assert site.serving() == SHA_OLD
    assert site.restart_policy(site.old_web) == "unless-stopped"
    assert site.restart_policy(site.old_api) == "unless-stopped"
    assert not any(name.startswith(site.project) for name in site.names())
    assert site.state()["current"] is None


def test_take_over_that_fails_restarts_the_old_containers(site):
    site.start_old_site()
    result = site.tool("live", tag(SHA_BAD), "--take-over", "--skip-trial", ready_seconds=4)
    assert result.returncode == 1
    assert "serving it again" in result.stdout
    assert site.serving() == SHA_OLD
    assert site.restart_policy(site.old_web) == "unless-stopped"
    assert site.restart_policy(site.old_api) == "unless-stopped"
    assert not any(name.startswith(site.project) for name in site.names())


def test_take_over_with_a_broken_release_never_touches_the_old_site(site):
    site.start_old_site()
    started = docker("inspect", "--format", "{{.State.StartedAt}}", site.old_web)
    result = site.tool("live", tag(SHA_BAD), "--take-over", ready_seconds=4)
    assert result.returncode == 1
    assert "the test start failed" in result.stdout
    assert site.serving() == SHA_OLD
    assert docker("inspect", "--format", "{{.State.StartedAt}}", site.old_web) == started


def test_later_releases_after_a_take_over_need_no_flag(site):
    site.start_old_site()
    assert site.tool("live", tag(SHA_A), "--take-over").returncode == 0
    result = site.tool("live", tag(SHA_B))
    assert result.returncode == 0, result.stdout + result.stderr
    assert site.serving() == SHA_B


# -- refusals -------------------------------------------------------------------

def test_api_and_web_images_from_different_builds_are_refused(site):
    result = site.tool("live", "mixed")
    assert result.returncode == 1
    assert "different builds" in result.stdout
    assert site.serving() is None


def test_release_that_is_not_on_the_machine_is_refused(site):
    result = site.tool("live", "git-" + "d" * 40)
    assert result.returncode == 1
    assert "is not on this machine" in result.stdout


def test_rollback_with_no_history_is_refused(site):
    result = site.tool("rollback")
    assert result.returncode == 2
    assert "no earlier release" in result.stdout


def test_restore_old_with_nothing_replaced_is_refused(site):
    assert site.tool("live", tag(SHA_A)).returncode == 0
    result = site.tool("restore-old")
    assert result.returncode == 2
    assert site.serving() == SHA_A


def test_two_deploys_cannot_run_at_once(site):
    site.state_dir.mkdir(parents=True)
    with open(site.state_dir / "lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        result = site.tool("live", tag(SHA_A))
    assert result.returncode == 2
    assert "another deploy is running" in result.stdout
    assert site.serving() is None


def test_status_describes_what_is_live(site):
    assert site.tool("live", tag(SHA_A)).returncode == 0
    result = site.tool("status")
    assert result.returncode == 0
    assert "Recorded live release:     build %s" % SHA_A[:12] in result.stdout
    assert "served by container %s-web" % site.project in result.stdout
    assert "status=ok database=connected build=%s" % SHA_A[:12] in result.stdout


# -- differences between the running site and its replacement ------------------------

def test_check_confirms_when_the_running_site_matches_what_would_replace_it(site):
    site.start_old_site()
    result = site.tool("check")
    assert result.returncode == 0, result.stdout
    assert "same network, and every setting of the live API is in the settings file" in result.stdout


def test_setting_missing_from_the_settings_file_is_named_but_never_shown(site):
    site.start_old_site("-e", "EXTRA_SECRET=do-not-print-this-value")
    result = site.tool("check")
    assert result.returncode == 1
    assert "has settings that are not in" in result.stdout and "EXTRA_SECRET" in result.stdout
    assert "do-not-print-this-value" not in result.stdout + result.stderr

    result = site.tool("live", tag(SHA_A), "--take-over")
    assert result.returncode == 2
    assert "EXTRA_SECRET" in result.stdout and "--accept-differences" in result.stdout
    assert "do-not-print-this-value" not in result.stdout + result.stderr
    assert site.serving() == SHA_OLD

    # once the settings file has it, there is nothing left to accept
    with open(site.env_file, "a") as handle:
        handle.write("# a comment\nexport EXTRA_SECRET=do-not-print-this-value\n")
    assert site.tool("check").returncode == 0
    result = site.tool("live", tag(SHA_A), "--take-over")
    assert result.returncode == 0, result.stdout
    assert site.serving() == SHA_A


def test_running_api_on_an_extra_network_blocks_the_take_over_until_accepted(site):
    extra = site.network + "-extra"
    docker("network", "create", extra)
    site.start_old_site()
    docker("network", "connect", extra, site.old_api)
    result = site.tool("check")
    assert result.returncode == 1
    assert "is also on Docker network(s) %s" % extra in result.stdout

    result = site.tool("live", tag(SHA_A), "--take-over")
    assert result.returncode == 2 and site.serving() == SHA_OLD

    result = site.tool("live", tag(SHA_A), "--take-over", "--accept-differences")
    assert result.returncode == 0, result.stdout
    assert site.serving() == SHA_A


# -- the spare address ------------------------------------------------------------

def test_spare_address_held_by_something_else_is_reported_and_never_disturbed(site):
    squatter = "edft-squatter-%s" % site.suffix
    docker("run", "-d", "--name", squatter, "--network", site.network,
           "-p", "127.0.0.1:%d:8000" % site.trial_port,
           "%s:git-%s" % (site.images.api, SHA_OLD))
    try:
        result = site.tool("check")
        assert result.returncode == 1
        assert squatter in result.stdout and "EDFINDER_TRIAL_BIND" in result.stdout

        result = site.tool("live", tag(SHA_A))
        assert result.returncode == 2
        assert "spare address" in result.stdout and squatter in result.stdout
        assert squatter in site.names()
        assert site.serving() is None
    finally:
        docker("rm", "-f", squatter, check=False)


def test_leftover_test_containers_of_this_tool_are_cleared_by_the_next_run(site):
    leftover = site.project + "-trial-web"
    docker("run", "-d", "--name", site.project + "-trial-api", "--network", site.network,
           "--label", "com.docker.compose.project=%s-trial" % site.project,
           "%s:git-%s" % (site.images.api, SHA_OLD))
    docker("run", "-d", "--name", leftover, "--network", site.network,
           "--label", "com.docker.compose.project=%s-trial" % site.project,
           "-e", "EDFINDER_API_UPSTREAM=%s-trial-api:8000" % site.project,
           "-p", "127.0.0.1:%d:8080" % site.trial_port,
           "%s:git-%s" % (site.images.web, SHA_OLD))
    result = site.tool("check")
    assert result.returncode == 0, result.stdout
    assert "leftover test of this tool" in result.stdout

    result = site.tool("live", tag(SHA_A))
    assert result.returncode == 0, result.stdout
    assert site.serving() == SHA_A
    assert leftover not in site.names()
