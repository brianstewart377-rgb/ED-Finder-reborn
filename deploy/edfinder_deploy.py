#!/usr/bin/env python3
"""Deploy ED-Finder on one machine with Docker Compose.

Commands:

    check          show what is running and whether this machine is ready
    try TAG        start TAG on a spare port, test it, then remove it again
    live TAG       test TAG on the spare port, then switch the site to it
    rollback       switch the site back to the release that was live before
    restore-old    stop this app and restart the containers it first replaced
    status         show what is live

TAG is an image tag: "main" for the newest build, or "git-<40 hex>" for an
exact one.

How "live" stays safe:

  1. The new release is first started on a spare port and tested there. If
     that fails, the live site is never touched.
  2. Only then is the live site switched. That takes a few seconds.
  3. If the switched site fails its checks, the previous release (or the
     containers this tool replaced) is put back automatically.

Settings come from the environment, then from a "deploy.conf" file beside this
script (KEY=VALUE lines), then from the defaults below.

Python standard library only. Needs Docker with the Compose plugin.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent

DEFAULTS: Dict[str, str] = {
    # Where the images are published.
    "EDFINDER_API_REPOSITORY": "ghcr.io/brianstewart377-rgb/ed-finder-reborn/api",
    "EDFINDER_WEB_REPOSITORY": "ghcr.io/brianstewart377-rgb/ed-finder-reborn/web",
    # Existing Docker network the database is on.
    "EDFINDER_NETWORK": "edfinder-v3-production",
    # File holding the API's settings and secrets. Never printed.
    "EDFINDER_API_ENV_FILE": "/etc/ed-finder/v3-production/api.env",
    # Address the public web server forwards to.
    "EDFINDER_LIVE_BIND": "127.0.0.1:58080",
    # Spare address used to test a release before it goes live.
    "EDFINDER_TRIAL_BIND": "127.0.0.1:58081",
    "EDFINDER_PROJECT": "edfinder",
    "EDFINDER_STATE_DIR": "/var/lib/edfinder-deploy",
    "EDFINDER_COMPOSE_FILE": str(HERE / "compose.yml"),
    # Seconds to wait for a started release to answer its health check.
    "EDFINDER_READY_SECONDS": "120",
    # Public address, checked for information after going live. Empty: skip.
    "EDFINDER_PUBLIC_ORIGIN": "https://ed-finder.app",
}

REVISION_LABEL = "org.opencontainers.image.revision"
SHA = re.compile(r"^[0-9a-f]{40}$")
TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
MAX_BODY = 4 * 1024 * 1024


class DeployError(Exception):
    """Something went wrong; the message says what, in plain words."""


class Refused(DeployError):
    """The tool declined to act; nothing was changed."""


# -- settings -------------------------------------------------------------------

def read_conf(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class Settings:
    def __init__(self, environ: Optional[Dict[str, str]] = None,
                 conf: Optional[Dict[str, str]] = None) -> None:
        environ = dict(os.environ if environ is None else environ)
        conf = read_conf(HERE / "deploy.conf") if conf is None else conf
        values = {key: environ.get(key) or conf.get(key) or default
                  for key, default in DEFAULTS.items()}
        # An explicitly empty public origin switches that check off.
        if environ.get("EDFINDER_PUBLIC_ORIGIN") == "" or conf.get("EDFINDER_PUBLIC_ORIGIN") == "":
            values["EDFINDER_PUBLIC_ORIGIN"] = ""
        self.api_repository = values["EDFINDER_API_REPOSITORY"]
        self.web_repository = values["EDFINDER_WEB_REPOSITORY"]
        self.network = values["EDFINDER_NETWORK"]
        self.api_env_file = values["EDFINDER_API_ENV_FILE"]
        self.live_bind = values["EDFINDER_LIVE_BIND"]
        self.trial_bind = values["EDFINDER_TRIAL_BIND"]
        self.project = values["EDFINDER_PROJECT"]
        self.state_dir = Path(values["EDFINDER_STATE_DIR"])
        self.compose_file = values["EDFINDER_COMPOSE_FILE"]
        self.public_origin = values["EDFINDER_PUBLIC_ORIGIN"].rstrip("/")
        try:
            self.ready_seconds = float(values["EDFINDER_READY_SECONDS"])
        except ValueError as error:
            raise DeployError("EDFINDER_READY_SECONDS must be a number") from error
        if not re.match(r"^[a-z0-9][a-z0-9_-]*$", self.project):
            raise DeployError("EDFINDER_PROJECT may only hold lowercase letters, digits, - and _")
        for bind in (self.live_bind, self.trial_bind):
            split_bind(bind)
        if self.live_bind == self.trial_bind:
            raise DeployError("the live and trial addresses must be different")

    @property
    def trial_project(self) -> str:
        return self.project + "-trial"


def split_bind(bind: str) -> Tuple[str, int]:
    host, separator, port = bind.rpartition(":")
    if not separator or not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise DeployError("address %r must look like 127.0.0.1:58080" % bind)
    return host, int(port)


def origin_of(bind: str) -> str:
    host, port = split_bind(bind)
    return "http://%s:%d" % (host, port)


# -- running commands -------------------------------------------------------------

def run(command: Sequence[str], *, env: Optional[Dict[str, str]] = None,
        check: bool = True, timeout: float = 900) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(list(command), env=env, capture_output=True, text=True,
                                timeout=timeout)
    except FileNotFoundError as error:
        raise DeployError("%s is not installed" % command[0]) from error
    except subprocess.TimeoutExpired as error:
        raise DeployError("timed out after %.0f s: %s" % (timeout, " ".join(command[:4]))) from error
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise DeployError("command failed: %s\n    %s" % (
            " ".join(command[:6]), "\n    ".join(detail[-8:]) or "(no output)"))
    return result


def docker(*args: str, check: bool = True, timeout: float = 900) -> str:
    return run(["docker", *args], check=check, timeout=timeout).stdout.strip()


def say(message: str = "") -> None:
    print(message, flush=True)


# -- releases -------------------------------------------------------------------

class Release:
    def __init__(self, tag: str, sha: str, api: str, web: str,
                 deployed_at: Optional[str] = None) -> None:
        self.tag, self.sha, self.api, self.web, self.deployed_at = tag, sha, api, web, deployed_at

    def to_json(self) -> Dict[str, Any]:
        return {"tag": self.tag, "sha": self.sha, "api": self.api, "web": self.web,
                "deployed_at": self.deployed_at}

    @classmethod
    def from_json(cls, value: Any) -> Optional["Release"]:
        if not isinstance(value, dict):
            return None
        try:
            release = cls(str(value["tag"]), str(value["sha"]), str(value["api"]),
                          str(value["web"]), value.get("deployed_at"))
        except KeyError:
            return None
        return release if SHA.match(release.sha) else None

    def describe(self) -> str:
        return "build %s" % self.sha[:12]


def image_revision(reference: str) -> str:
    value = docker("image", "inspect", "--format",
                   '{{ index .Config.Labels "%s" }}' % REVISION_LABEL, reference, check=False)
    return value.strip()


def resolve_release(settings: Settings, tag: str, pull: bool) -> Release:
    """Fetch both images for `tag` and pin them to the exact build they hold."""
    if not TAG.match(tag):
        raise DeployError("%r is not a valid image tag" % tag)
    references = {"api": "%s:%s" % (settings.api_repository, tag),
                  "web": "%s:%s" % (settings.web_repository, tag)}
    revisions = {}
    for name, reference in references.items():
        if pull:
            say("Fetching %s ..." % reference)
            docker("pull", "--quiet", reference)
        revision = image_revision(reference)
        if not SHA.match(revision):
            raise DeployError(
                "image %s %s" % (reference, "is not on this machine" if not revision
                                 else "does not say which build it is"))
        revisions[name] = revision
    if revisions["api"] != revisions["web"]:
        raise DeployError(
            "the API and web images for %r are different builds (%s and %s); "
            "wait for both to finish building and try again" % (
                tag, revisions["api"][:12], revisions["web"][:12]))
    sha = revisions["api"]
    pinned_tag = "git-" + sha
    pinned = {"api": "%s:%s" % (settings.api_repository, pinned_tag),
              "web": "%s:%s" % (settings.web_repository, pinned_tag)}
    for name in ("api", "web"):
        if references[name] != pinned[name]:
            # Keep an exact local name, so a later rollback finds this build
            # even after the moving tag has moved on.
            docker("tag", references[name], pinned[name])
    return Release(pinned_tag, sha, pinned["api"], pinned["web"])


# -- containers -----------------------------------------------------------------

def running_containers() -> List[Dict[str, Any]]:
    ids = docker("ps", "-q", "--no-trunc").split()
    if not ids:
        return []
    return json.loads(docker("inspect", *ids))


def container_name(container: Dict[str, Any]) -> str:
    return str(container.get("Name", "")).lstrip("/")


def publishes(container: Dict[str, Any], bind: str) -> bool:
    host, port = split_bind(bind)
    ports = (container.get("NetworkSettings") or {}).get("Ports") or {}
    for bindings in ports.values():
        for binding in bindings or []:
            if str(binding.get("HostPort")) != str(port):
                continue
            if binding.get("HostIp") in (host, "0.0.0.0", "", "::", None):
                return True
    return False


def port_holders(bind: str) -> List[Dict[str, Any]]:
    return [container for container in running_containers() if publishes(container, bind)]


def port_is_free(bind: str) -> bool:
    host, port = split_bind(bind)
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def upstream_partner(container: Dict[str, Any]) -> Optional[str]:
    """Name of the API container a web container forwards to, if it says."""
    for entry in (container.get("Config") or {}).get("Env") or []:
        key, _, value = str(entry).partition("=")
        if key == "EDFINDER_API_UPSTREAM" and value:
            return value.rpartition(":")[0] or value
    return None


def restart_policy(container: Dict[str, Any]) -> str:
    policy = ((container.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name") or "no"
    return policy if policy in ("no", "always", "unless-stopped", "on-failure") else "no"


def logs_tail(name: str, lines: int = 15) -> str:
    result = run(["docker", "logs", "--tail", str(lines), name], check=False, timeout=30)
    text = (result.stdout or "") + (result.stderr or "")
    return "\n".join("      " + line for line in text.strip().splitlines()[-lines:])


# Settings the compose file gives the API itself, on top of the settings file.
COMPOSE_API_SETTINGS = frozenset({
    "BUILD_SHA", "ADMIN_OPERATION_STARTUP_REAP_ENABLED", "EDDN_SIMULATION_INGEST_ENABLED"})


def setting_names(entries: Any) -> set:
    return {str(entry).partition("=")[0] for entry in entries or [] if str(entry).strip()}


def env_file_names(path: str) -> set:
    """The names (never the values) of the settings in an env file."""
    names = set()
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        names.add(line.partition("=")[0].strip())
    return names


def image_setting_names(image: str) -> set:
    raw = docker("image", "inspect", "--format", "{{json .Config.Env}}", image, check=False)
    try:
        return setting_names(json.loads(raw))
    except ValueError:
        return set()


def takeover_concerns(settings: Settings) -> List[str]:
    """Ways the site now serving the live address differs from what this tool
    would start. Compares names only; no setting value is ever read or shown."""
    concerns: List[str] = []
    containers = running_containers()
    by_name = {container_name(c): c for c in containers}
    ours = settings.project + "-web"
    for holder in containers:
        if not publishes(holder, settings.live_bind) or container_name(holder) == ours:
            continue
        partner = upstream_partner(holder)
        api = by_name.get(partner) if partner else None
        if api is None:
            concerns.append(
                "cannot tell which API container %s forwards to, so its settings "
                "could not be compared" % container_name(holder))
            continue
        networks = set(((api.get("NetworkSettings") or {}).get("Networks") or {}).keys())
        if settings.network not in networks:
            concerns.append("the live API %s is not on Docker network %s (it is on: %s)" % (
                partner, settings.network, ", ".join(sorted(networks)) or "none"))
        extra = sorted(networks - {settings.network})
        if extra:
            concerns.append(
                "the live API %s is also on Docker network(s) %s; the new API would only "
                "join %s" % (partner, ", ".join(extra), settings.network))
        try:
            in_file = env_file_names(settings.api_env_file)
        except OSError:
            concerns.append("the API settings file %s could not be read" % settings.api_env_file)
            continue
        live = setting_names((api.get("Config") or {}).get("Env"))
        explained = in_file | COMPOSE_API_SETTINGS | image_setting_names(
            str((api.get("Config") or {}).get("Image")))
        unexplained = sorted(live - explained)
        if unexplained:
            concerns.append(
                "the live API %s has settings that are not in %s (names only): %s" % (
                    partner, settings.api_env_file, ", ".join(unexplained)))
    return concerns


# -- compose --------------------------------------------------------------------

def compose_env(settings: Settings, project: str, api: str, web: str, bind: str,
                restart: str) -> Dict[str, str]:
    env = dict(os.environ)
    env.update({
        "EDFINDER_PROJECT": project,
        "EDFINDER_API_IMAGE": api,
        "EDFINDER_WEB_IMAGE": web,
        "EDFINDER_API_ENV_FILE": settings.api_env_file,
        "EDFINDER_WEB_BIND": bind,
        "EDFINDER_NETWORK": settings.network,
        "EDFINDER_RESTART": restart,
    })
    return env


def compose_up(settings: Settings, project: str, release: Release, bind: str,
               restart: str) -> None:
    env = compose_env(settings, project, release.api, release.web, bind, restart)
    run(["docker", "compose", "-f", settings.compose_file, "-p", project,
         "up", "-d", "--no-build", "--pull", "never", "--force-recreate", "--remove-orphans"],
        env=env)


def compose_down(settings: Settings, project: str) -> None:
    env = compose_env(settings, project, "unused", "unused", settings.trial_bind, "no")
    run(["docker", "compose", "-f", settings.compose_file, "-p", project,
         "down", "--remove-orphans", "--timeout", "30"], env=env, check=False)


# -- checking a running release ----------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def http_get(origin: str, path: str, timeout: float = 10) -> Tuple[int, bytes]:
    request = urllib.request.Request(origin + path, headers={"User-Agent": "edfinder-deploy/1"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read(MAX_BODY + 1)
    except urllib.error.HTTPError as error:
        with error:  # an error answer still holds an open connection
            return error.code, error.read(MAX_BODY + 1)
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as error:
        raise DeployError("no answer from %s%s (%s)" % (origin, path, error)) from error


def health_of(origin: str) -> Dict[str, Any]:
    status, body = http_get(origin, "/api/health")
    if status != 200:
        raise DeployError("%s/api/health answered %d" % (origin, status))
    try:
        value = json.loads(body)
    except ValueError as error:
        raise DeployError("%s/api/health did not answer with JSON" % origin) from error
    if not isinstance(value, dict):
        raise DeployError("%s/api/health did not answer with an object" % origin)
    return value


def health_problem(health: Dict[str, Any], sha: Optional[str]) -> Optional[str]:
    if health.get("status") != "ok":
        return "status is %r, not ok" % health.get("status")
    if health.get("database") != "connected":
        return "database is %r, not connected" % health.get("database")
    if sha is not None and health.get("build_sha") != sha:
        return "it reports build %s, expected %s" % (
            str(health.get("build_sha"))[:12], sha[:12])
    return None


def wait_ready(origin: str, sha: Optional[str], seconds: float) -> None:
    """Wait until the release at `origin` is healthy (and is build `sha`)."""
    deadline = time.monotonic() + seconds
    problem = "it never answered"
    while True:
        try:
            problem = health_problem(health_of(origin), sha)
            if problem is None:
                return
        except DeployError as error:
            problem = str(error)
        if time.monotonic() >= deadline:
            raise DeployError("not healthy after %.0f s: %s" % (seconds, problem))
        time.sleep(1)


def smoke(origin: str, sha: str) -> None:
    """The checks a release must pass: page, health, API description, sign-in state."""
    bodies: Dict[str, bytes] = {}
    for path in ("/", "/api/health", "/openapi.json", "/api/v1/auth/session"):
        status, body = http_get(origin, path)
        if not 200 <= status < 300:
            raise DeployError("%s answered %d" % (path, status))
        if len(body) > MAX_BODY:
            raise DeployError("%s answered with too much data" % path)
        bodies[path] = body
    try:
        health = json.loads(bodies["/api/health"])
        session = json.loads(bodies["/api/v1/auth/session"])
        openapi = json.loads(bodies["/openapi.json"])
    except ValueError as error:
        raise DeployError("the API did not answer with JSON") from error
    problem = health_problem(health if isinstance(health, dict) else {}, sha)
    if problem:
        raise DeployError("health check: " + problem)
    if not isinstance(session, dict) or session.get("authenticated") is not False \
            or session.get("user") is not None:
        raise DeployError("a visitor who is not signed in was not reported as signed out")
    paths = openapi.get("paths") if isinstance(openapi, dict) else None
    if not isinstance(paths, dict) or not {"/api/health", "/api/v1/auth/session"} <= set(paths):
        raise DeployError("the API description is missing expected routes")
    marker = ('name="edfinder-build-sha" content="%s"' % sha).encode()
    if bodies["/"].count(marker) != 1:
        raise DeployError("the web page is not build %s" % sha[:12])


# -- state ----------------------------------------------------------------------

class State:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "state.json"
        self.current: Optional[Release] = None
        self.previous: Optional[Release] = None
        self.original_containers: List[Dict[str, str]] = []
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError as error:
                raise DeployError("%s is damaged; move it aside and run again" % self.path) from error
            self.current = Release.from_json(data.get("current"))
            self.previous = Release.from_json(data.get("previous"))
            self.original_containers = [
                {"name": str(item["name"]), "restart": str(item.get("restart", "no"))}
                for item in data.get("original_containers") or []
                if isinstance(item, dict) and item.get("name")]

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        data = {
            "current": self.current.to_json() if self.current else None,
            "previous": self.previous.to_json() if self.previous else None,
            "original_containers": self.original_containers,
        }
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)

    def log(self, message: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(self.directory / "deploy.log", "a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (stamp, message))


@contextlib.contextmanager
def single_run(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / "lock", "w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise Refused("another deploy is running on this machine") from error
        yield


# -- the steps ------------------------------------------------------------------

def trial(settings: Settings, release: Release) -> None:
    """Start the release on the spare address, test it, and remove it again."""
    project = settings.trial_project
    compose_down(settings, project)  # clear anything a crashed run left behind
    others = [container_name(c) for c in port_holders(settings.trial_bind)]
    if others:
        raise Refused("the spare address %s is in use by %s" % (
            settings.trial_bind, ", ".join(others)))
    if not port_is_free(settings.trial_bind):
        raise Refused("the spare address %s is in use by a program outside Docker"
                      % settings.trial_bind)
    say("Testing %s on the spare address %s ..." % (release.describe(), settings.trial_bind))
    try:
        compose_up(settings, project, release, settings.trial_bind, "no")
        wait_ready(origin_of(settings.trial_bind), release.sha, settings.ready_seconds)
        smoke(origin_of(settings.trial_bind), release.sha)
    except DeployError as error:
        tail = logs_tail(project + "-api")
        raise DeployError("the test start failed: %s\n    Last lines from its API:\n%s" % (
            error, tail or "      (none)")) from error
    finally:
        compose_down(settings, project)
    say("  passed: page, health, API description and sign-in state all correct.")


def stop_originals(state: State, containers: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Stop the containers this app replaces, remembering how to restart them."""
    names = [container_name(container) for container in containers]
    targets: List[Dict[str, str]] = []
    by_name = {container_name(c): c for c in running_containers()}
    for container in containers:
        partner = upstream_partner(container)
        if partner and partner in by_name and partner not in names \
                and partner not in [t["name"] for t in targets]:
            targets.append({"name": partner, "restart": restart_policy(by_name[partner])})
    for container in containers:
        targets.append({"name": container_name(container), "restart": restart_policy(container)})
    # Written down before anything is stopped, so "restore-old" works even if
    # this run is interrupted half way.
    known = {item["name"] for item in state.original_containers}
    state.original_containers += [t for t in targets if t["name"] not in known]
    state.save()
    for target in reversed(targets):  # web first, so nothing is served half-broken
        say("  stopping %s (kept, not deleted)" % target["name"])
        docker("update", "--restart=no", target["name"])
        docker("stop", "--time", "30", target["name"], timeout=120)
    return targets


def start_originals(settings: Settings, originals: List[Dict[str, str]]) -> None:
    for item in originals:  # API first, then web
        say("  starting %s" % item["name"])
        docker("update", "--restart=%s" % item["restart"], item["name"])
        docker("start", item["name"], timeout=120)
    wait_ready(origin_of(settings.live_bind), None, settings.ready_seconds)


def foreign_holders(settings: Settings, take_over: bool,
                    accept_differences: bool) -> List[Dict[str, Any]]:
    """Containers serving the live address that this tool did not start.

    Raises Refused, before anything is changed, when replacing them has not
    been asked for or the old and new sites differ in a way not yet accepted.
    """
    live_web = settings.project + "-web"
    holders = port_holders(settings.live_bind)
    foreign = [c for c in holders if container_name(c) != live_web]
    if foreign and not take_over:
        raise Refused(
            "the live address %s is served by %s, which this tool did not start.\n"
            "    To replace it, run the same command again with --take-over added.\n"
            "    Those containers are stopped, not deleted, and \"restore-old\" restarts them."
            % (settings.live_bind, ", ".join(container_name(c) for c in foreign)))
    if foreign and not accept_differences:
        concerns = takeover_concerns(settings)
        if concerns:
            raise Refused(
                "the site running now differs from what would replace it:\n    - %s\n"
                "    If these are fine, add --accept-differences." % "\n    - ".join(concerns))
    if not holders and not port_is_free(settings.live_bind):
        raise Refused("the live address %s is in use by a program outside Docker"
                      % settings.live_bind)
    return foreign


def switch(settings: Settings, state: State, release: Release, take_over: bool,
           accept_differences: bool = False) -> None:
    """Put `release` on the live address, restoring what was there if it fails."""
    foreign = foreign_holders(settings, take_over, accept_differences)

    replaced: List[Dict[str, str]] = []
    say("Switching the live site to %s ..." % release.describe())
    try:
        if foreign:
            replaced = stop_originals(state, foreign)
        compose_up(settings, settings.project, release, settings.live_bind, "unless-stopped")
        wait_ready(origin_of(settings.live_bind), release.sha, settings.ready_seconds)
        smoke(origin_of(settings.live_bind), release.sha)
    except DeployError as error:
        tail = logs_tail(settings.project + "-api")
        say("  FAILED: %s" % error)
        if tail:
            say("    Last lines from its API:\n%s" % tail)
        state.log("FAILED live %s: %s" % (release.tag, str(error).splitlines()[0]))
        outcome = recover(settings, state, release, replaced)
        raise DeployError("%s did not go live. %s" % (release.describe(), outcome)) from error


def recover(settings: Settings, state: State, failed: Release,
            replaced: List[Dict[str, str]]) -> str:
    """After a failed switch, put back whatever was serving the site before.

    Returns a sentence saying what the site is running now.
    """
    try:
        if replaced:
            say("Putting the previous containers back ...")
            compose_down(settings, settings.project)
            start_originals(settings, replaced)
            state.log("restored original containers after failed %s" % failed.tag)
            return "The containers that were serving the site before are serving it again."
        if state.current and state.current.sha != failed.sha:
            say("Rolling back to %s ..." % state.current.describe())
            compose_up(settings, settings.project, state.current, settings.live_bind,
                       "unless-stopped")
            wait_ready(origin_of(settings.live_bind), state.current.sha, settings.ready_seconds)
            state.log("rolled back to %s after failed %s" % (state.current.tag, failed.tag))
            return "%s is serving the site again." % state.current.describe()
    except DeployError as error:
        state.log("RECOVERY FAILED after %s: %s" % (failed.tag, str(error).splitlines()[0]))
        raise DeployError(
            "THE SITE IS DOWN. %s failed, and putting the previous release back also "
            "failed: %s\n    Run \"status\" to see what is running, then \"rollback\" or "
            "\"restore-old\"." % (failed.describe(), error)) from error
    state.log("no earlier release to return to after failed %s" % failed.tag)
    return ("There was no earlier release to go back to, so its containers were left "
            "running for you to inspect.")


def public_report(settings: Settings, sha: str) -> None:
    if not settings.public_origin:
        return
    try:
        health = health_of(settings.public_origin)
    except DeployError as error:
        say("Could not check the public site: %s" % str(error).splitlines()[0])
        return
    seen = str(health.get("build_sha"))
    if seen == sha:
        say("The public site %s is serving this build." % settings.public_origin)
    else:
        say("Note: the public site %s reports build %s, not %s. It may be served from "
            "somewhere else or cached." % (settings.public_origin, seen[:12], sha[:12]))


# -- commands -------------------------------------------------------------------

def command_try(settings: Settings, tag: str, pull: bool) -> None:
    release = resolve_release(settings, tag, pull)
    trial(settings, release)
    say("OK: %s works on this machine. The live site was not touched." % release.describe())


def command_live(settings: Settings, tag: str, pull: bool, take_over: bool,
                 skip_trial: bool, accept_differences: bool = False) -> None:
    state = State(settings.state_dir)
    # Refuse now, before fetching or testing anything, if the switch itself
    # would be refused.
    foreign_holders(settings, take_over, accept_differences)
    release = resolve_release(settings, tag, pull)
    if not skip_trial:
        trial(settings, release)
    previous = state.current
    switch(settings, state, release, take_over, accept_differences)
    release.deployed_at = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    if previous and previous.sha != release.sha:
        state.previous = previous
    state.current = release
    state.save()
    state.log("LIVE %s" % release.tag)
    say("LIVE: the site is now running %s." % release.describe())
    public_report(settings, release.sha)


def command_rollback(settings: Settings) -> None:
    state = State(settings.state_dir)
    if not state.previous:
        raise Refused("there is no earlier release recorded to roll back to")
    target, leaving = state.previous, state.current
    if not SHA.match(image_revision(target.api)) or not SHA.match(image_revision(target.web)):
        raise Refused("the images for %s are no longer on this machine; "
                      "use \"live %s\" to fetch and start them" % (target.describe(), target.tag))
    switch(settings, state, target, take_over=False)
    target.deployed_at = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    state.current, state.previous = target, leaving
    state.save()
    state.log("ROLLBACK to %s" % target.tag)
    say("LIVE: rolled back; the site is now running %s." % target.describe())
    public_report(settings, target.sha)


def command_restore_old(settings: Settings) -> None:
    state = State(settings.state_dir)
    if not state.original_containers:
        raise Refused("this tool has not replaced any earlier containers on this machine")
    say("Stopping this app and restarting the containers it replaced ...")
    compose_down(settings, settings.project)
    start_originals(settings, state.original_containers)
    if state.current:
        state.previous = state.current
    state.current = None
    state.save()
    state.log("RESTORED original containers")
    say("RESTORED: %s are serving the site again." % ", ".join(
        item["name"] for item in state.original_containers))


def describe_live(settings: Settings) -> List[str]:
    lines: List[str] = []
    holders = port_holders(settings.live_bind)
    if not holders:
        lines.append("Nothing in Docker is serving the live address %s." % settings.live_bind)
    for container in holders:
        partner = upstream_partner(container)
        lines.append("Live address %s is served by container %s" % (
            settings.live_bind, container_name(container)))
        lines.append("    image: %s" % (container.get("Config") or {}).get("Image"))
        lines.append("    restarts: %s" % restart_policy(container))
        if partner:
            lines.append("    forwards API calls to: %s" % partner)
    try:
        health = health_of(origin_of(settings.live_bind))
        lines.append("Health at the live address: status=%s database=%s build=%s" % (
            health.get("status"), health.get("database"), str(health.get("build_sha"))[:12]))
    except DeployError as error:
        lines.append("Health at the live address: %s" % str(error).splitlines()[0])
    return lines


def command_status(settings: Settings) -> None:
    state = State(settings.state_dir)
    say("Recorded live release:     %s" % (
        "%s, since %s" % (state.current.describe(), state.current.deployed_at)
        if state.current else "none"))
    say("Recorded previous release: %s" % (
        state.previous.describe() if state.previous else "none"))
    if state.original_containers:
        say("Containers this app replaced (stopped, kept): %s" % ", ".join(
            item["name"] for item in state.original_containers))
    for line in describe_live(settings):
        say(line)


def command_check(settings: Settings) -> None:
    problems: List[str] = []
    say("Docker:  %s" % docker("version", "--format", "{{.Server.Version}}"))
    compose_version = run(["docker", "compose", "version", "--short"], check=False)
    if compose_version.returncode != 0:
        problems.append("the Docker Compose plugin is missing")
    else:
        say("Compose: %s" % compose_version.stdout.strip())
    if not Path(settings.compose_file).is_file():
        problems.append("compose file %s is missing" % settings.compose_file)
    else:
        say("Compose file: %s" % settings.compose_file)
    if run(["docker", "network", "inspect", settings.network], check=False).returncode != 0:
        problems.append("Docker network %s does not exist" % settings.network)
    else:
        say("Network %s: present" % settings.network)
    env_file = Path(settings.api_env_file)
    try:
        mode = env_file.stat().st_mode
        if not env_file.is_file():
            problems.append("%s is not a file" % env_file)
        else:
            say("API settings file %s: present (contents not shown)" % env_file)
            if mode & 0o077:
                say("    note: other users on this machine can read it (mode %o)" % (mode & 0o777))
    except OSError:
        problems.append("API settings file %s is missing or unreadable" % env_file)
    for line in describe_live(settings):
        say(line)
    concerns = takeover_concerns(settings) if not problems else []
    if concerns:
        problems.append("the site running now differs from what would replace it:\n      - "
                        + "\n      - ".join(concerns))
    elif any(container_name(c) != settings.project + "-web"
             for c in port_holders(settings.live_bind)):
        say("Compared with the site running now: same network, and every setting of the "
            "live API is in the settings file.")
    trial_users = [container_name(c) for c in port_holders(settings.trial_bind)]
    if trial_users:
        say("Spare address %s is in use by %s (a leftover test is cleared automatically)" % (
            settings.trial_bind, ", ".join(trial_users)))
    elif not port_is_free(settings.trial_bind):
        problems.append("spare address %s is in use by a program outside Docker"
                        % settings.trial_bind)
    else:
        say("Spare address %s: free" % settings.trial_bind)
    state = State(settings.state_dir)
    say("Recorded live release: %s" % (state.current.describe() if state.current else "none yet"))
    if problems:
        raise DeployError("not ready:\n    - " + "\n    - ".join(problems))
    say("OK: this machine is ready. Nothing was changed.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="edfinder_deploy.py", description="Deploy ED-Finder with Docker Compose.")
    parser.add_argument("--no-pull", action="store_true",
                        help="use images already on this machine instead of fetching them")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="show whether this machine is ready; changes nothing")
    commands.add_parser("status", help="show what is live; changes nothing")
    try_parser = commands.add_parser("try", help="test a release on the spare address only")
    try_parser.add_argument("tag")
    live_parser = commands.add_parser("live", help="test a release, then switch the site to it")
    live_parser.add_argument("tag")
    live_parser.add_argument("--take-over", action="store_true",
                             help="replace containers this tool did not start (they are kept)")
    live_parser.add_argument("--skip-trial", action="store_true",
                             help="switch without the test on the spare address")
    live_parser.add_argument("--accept-differences", action="store_true",
                             help="replace the running site even though \"check\" lists "
                                  "differences from it")
    commands.add_parser("rollback", help="switch back to the previous release")
    commands.add_parser("restore-old", help="restart the containers this tool first replaced")
    args = parser.parse_args(argv)

    try:
        settings = Settings()
        if args.command in ("check", "status"):
            (command_check if args.command == "check" else command_status)(settings)
            return 0
        with single_run(settings.state_dir):
            if args.command == "try":
                command_try(settings, args.tag, not args.no_pull)
            elif args.command == "live":
                command_live(settings, args.tag, not args.no_pull, args.take_over,
                             args.skip_trial, args.accept_differences)
            elif args.command == "rollback":
                command_rollback(settings)
            elif args.command == "restore-old":
                command_restore_old(settings)
        return 0
    except Refused as error:
        say("REFUSED, nothing was changed: %s" % error)
        return 2
    except DeployError as error:
        say("FAILED: %s" % error)
        return 1
    except KeyboardInterrupt:
        say("INTERRUPTED. Run \"status\" to see what is running.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
