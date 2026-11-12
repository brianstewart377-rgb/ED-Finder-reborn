#!/usr/bin/env python3
"""Write the API's settings file for the Raspberry Pi: /etc/edfinder/api.env.

Reads the current server's settings file on standard input and writes the
same settings for the Pi, except three:

  DATABASE_URL           the Pi's own database, with the password from
                         /etc/edfinder/postgres-password
  REDIS_URL              the Pi's own cache (deploy/pi/redis.yml)
  DATABASE_READONLY_URL  left out: the Pi has no separate read-only login,
                         and without it the API reads through DATABASE_URL

Everything else (the Frontier sign-in settings, the owner's token, the site's
address and so on) is kept as it is. Nothing is shown on the screen: only the
names of the settings.

Run on the Pi, as the user pi. The server's file comes straight down the Pi's
key for the move, and is written by root, readable by root only:

    ssh -i ~/.ssh/edf_move root@nb79a3d.mevnode.com cat /etc/ed-finder/v3-production/api.env | sudo python3 /opt/edfinder/make_api_env.py

Settings come from the environment (EDFINDER_PI_*); the defaults are the Pi's.
Standard library only; needs Python 3.9 or newer.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

DEFAULTS: Dict[str, str] = {
    "EDFINDER_PI_API_ENV": "/etc/edfinder/api.env",
    "EDFINDER_PI_DB_PASSWORD_FILE": "/etc/edfinder/postgres-password",
    # The database as the API reaches it over the "edfinder" Docker network.
    "EDFINDER_PI_DB_HOST": "edfinder-v3-phase4c-full-20260827_r5-postgres",
    "EDFINDER_PI_DB_USER": "edfinder_v3",
    "EDFINDER_PI_DB_NAME": "edfinder_v3_phase4c_full_20260827_r5",
    "EDFINDER_PI_REDIS_URL": "redis://edfinder-redis:6379/0",
}

# A line that sets a setting: NAME=value, perhaps after "export ".
SETTING = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
# Without these, what came in is not the API's settings file (an ssh that
# failed, for example, sends nothing).
EXPECTED = ("DATABASE_URL", "CORS_ORIGINS")
LEFT_OUT = ("DATABASE_READONLY_URL",)


class Refused(Exception):
    """A plain message for the person running it."""


def database_url(values: Dict[str, str], password: str) -> str:
    return "postgresql://%s:%s@%s:5432/%s" % (
        quote(values["EDFINDER_PI_DB_USER"], safe=""), quote(password, safe=""),
        values["EDFINDER_PI_DB_HOST"], quote(values["EDFINDER_PI_DB_NAME"], safe=""))


def for_the_pi(server: str, replacements: Dict[str, str]) -> Tuple[str, List[str], List[str]]:
    """The server's settings file rewritten for the Pi, the names of its
    settings, and the names left out."""
    names: List[str] = []
    left_out: List[str] = []
    lines: List[str] = []
    for line in server.splitlines():
        found = SETTING.match(line)
        if not found:
            lines.append(line)
            continue
        name = found.group(1)
        if name in LEFT_OUT:
            left_out.append(name)
            continue
        if name in replacements:
            if name in names:
                continue  # said twice: the replacement is written once
            line = "%s=%s" % (name, replacements[name])
        names.append(name)
        lines.append(line)
    missing = [name for name in EXPECTED if name not in names]
    if missing:
        raise Refused("what came in is not the API's settings file (it has no %s). Nothing was written."
                      % " or ".join(missing))
    for name, value in replacements.items():
        if name not in names:
            names.append(name)
            lines.append("%s=%s" % (name, value))
    return "\n".join(lines) + "\n", names, left_out


def write_private(path: Path, text: str) -> None:
    """Write the file so that only its owner can ever read it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, partial = tempfile.mkstemp(prefix=".api.env.", dir=str(path.parent))
    try:
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(partial, path)
    except BaseException:
        try:
            os.unlink(partial)
        except FileNotFoundError:
            pass
        raise


def main(argv: Optional[Sequence[str]] = None, environ: Optional[Dict[str, str]] = None,
         stdin=None) -> int:
    source = os.environ if environ is None else environ
    values = {key: source.get(key, default) for key, default in DEFAULTS.items()}
    stdin = sys.stdin if stdin is None else stdin
    try:
        if getattr(stdin, "isatty", lambda: False)():
            raise Refused("the server's settings file must come in on standard input. Run it as shown "
                          "at the top of make_api_env.py.")
        password_file = Path(values["EDFINDER_PI_DB_PASSWORD_FILE"])
        try:
            password = password_file.read_text(encoding="utf-8").rstrip("\r\n")
        except PermissionError:
            raise Refused("%s can only be read by root. Run this with sudo." % password_file) from None
        except FileNotFoundError:
            raise Refused("%s does not exist: is PostgreSQL set up on this machine?" % password_file) from None
        if not password:
            raise Refused("%s is empty." % password_file)
        text, names, left_out = for_the_pi(stdin.read(), {
            "DATABASE_URL": database_url(values, password),
            "REDIS_URL": values["EDFINDER_PI_REDIS_URL"],
        })
        target = Path(values["EDFINDER_PI_API_ENV"])
        write_private(target, text)
    except Refused as error:
        print("STOPPED: %s" % error, file=sys.stderr)
        return 1
    print("Wrote %s (readable by %s only): %d settings." % (
        target, "root" if os.geteuid() == 0 else "this user", len(names)))
    print("  Set for the Pi: DATABASE_URL (its own database), REDIS_URL (its own cache).")
    if left_out:
        print("  Left out: %s." % ", ".join(left_out))
    print("  Kept as on the server: %s." % ", ".join(
        sorted(name for name in names if name not in ("DATABASE_URL", "REDIS_URL"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
