"""scripts/pi/make_api_env.py: the server's API settings, rewritten for the Pi.

The settings below are stand-ins written for these tests; the real file holds
secrets and never leaves the machines.
"""
from __future__ import annotations

import importlib.util
import io
import stat
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("make_api_env", ROOT / "scripts" / "pi" / "make_api_env.py")
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

SERVER = """# API settings (current server)
DATABASE_URL=postgresql://edfinder_v3:server-password@edfinder-v3-phase4c-full-20260827_r5-postgres:5432/edfinder_v3_phase4c_full_20260827_r5
DATABASE_READONLY_URL=postgresql://reader:reader-password@db:5432/edfinder
REDIS_URL=redis://edfinder-v3-redis:6379/0
CORS_ORIGINS=https://ed-finder.app
FRONTIER_CLIENT_ID=frontier-id-for-tests
FRONTIER_CLIENT_SECRET="frontier secret for tests"
ADMIN_TOKEN=owner-token-for-tests

export SENTRY_DSN=
"""
PASSWORD = "p@ss/w:rd+=%#&"


class Pipe(io.StringIO):
    def isatty(self) -> bool:
        return False


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture
def machine(tmp_path):
    password = tmp_path / "postgres-password"
    password.write_text(PASSWORD + "\n")
    target = tmp_path / "etc" / "api.env"
    return {"EDFINDER_PI_API_ENV": str(target), "EDFINDER_PI_DB_PASSWORD_FILE": str(password)}


def settings_in(text: str) -> dict:
    found = {}
    for line in text.splitlines():
        match = tool.SETTING.match(line)
        if match:
            found[match.group(1)] = line.split("=", 1)[1]
    return found


def test_the_pi_gets_its_own_database_and_cache_and_keeps_everything_else(machine, capsys):
    assert tool.main(environ=machine, stdin=Pipe(SERVER)) == 0
    target = Path(machine["EDFINDER_PI_API_ENV"])
    written = target.read_text()
    settings = settings_in(written)
    url = urlsplit(settings["DATABASE_URL"])
    assert (url.scheme, url.username, url.hostname, url.port, url.path) == (
        "postgresql", "edfinder_v3", "edfinder-v3-phase4c-full-20260827_r5-postgres", 5432,
        "/edfinder_v3_phase4c_full_20260827_r5")
    assert unquote(url.password) == PASSWORD
    assert settings["REDIS_URL"] == "redis://edfinder-redis:6379/0"
    assert "DATABASE_READONLY_URL" not in settings
    for line in ("CORS_ORIGINS=https://ed-finder.app", "FRONTIER_CLIENT_ID=frontier-id-for-tests",
                 'FRONTIER_CLIENT_SECRET="frontier secret for tests"', "ADMIN_TOKEN=owner-token-for-tests",
                 "export SENTRY_DSN=", "# API settings (current server)"):
        assert line in written.splitlines()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    shown = capsys.readouterr()
    for secret in ("server-password", "reader-password", "frontier secret", "owner-token", PASSWORD,
                   "frontier-id-for-tests"):
        assert secret not in shown.out + shown.err
    assert "Left out: DATABASE_READONLY_URL." in shown.out
    assert "ADMIN_TOKEN" in shown.out  # names are shown, never values


def test_a_setting_said_twice_is_written_once_and_a_missing_cache_address_is_added(machine):
    server = SERVER.replace("REDIS_URL=redis://edfinder-v3-redis:6379/0\n", "") + \
        "DATABASE_URL=postgresql://old@somewhere/else\n"
    assert tool.main(environ=machine, stdin=Pipe(server)) == 0
    written = Path(machine["EDFINDER_PI_API_ENV"]).read_text()
    assert written.count("DATABASE_URL=") == 1 and written.count("REDIS_URL=") == 1


@pytest.mark.parametrize("server", ["", "Permission denied (publickey).\n", "CORS_ORIGINS=x\n"])
def test_anything_but_the_api_settings_file_writes_nothing(machine, server, capsys):
    assert tool.main(environ=machine, stdin=Pipe(server)) == 1
    assert "not the API's settings file" in capsys.readouterr().err
    assert not Path(machine["EDFINDER_PI_API_ENV"]).exists()


def test_it_waits_for_nothing_when_no_file_is_piped_in(machine, capsys):
    assert tool.main(environ=machine, stdin=Terminal()) == 1
    assert "must come in on standard input" in capsys.readouterr().err


def test_without_the_database_password_it_writes_nothing(machine, capsys):
    Path(machine["EDFINDER_PI_DB_PASSWORD_FILE"]).unlink()
    assert tool.main(environ=machine, stdin=Pipe(SERVER)) == 1
    assert "does not exist" in capsys.readouterr().err
    assert not Path(machine["EDFINDER_PI_API_ENV"]).exists()
