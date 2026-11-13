#!/usr/bin/env python3
"""Write the EDDN listener's settings file for the Raspberry Pi: /etc/edfinder/eddn.env.

The listener (deploy/pi/eddn.yml) needs one setting: where the database is,
with its password. This writes that one line, with the password from
/etc/edfinder/postgres-password, into a file only root can read. Nothing is
shown on the screen but the name of the setting.

It is a file of its own, and not the API's, so that the listener is never
given the API's other settings (the owner's token, the sign-in secrets).

Run on the Pi:

    sudo python3 /opt/edfinder/make_eddn_env.py

Running it again writes the same file again. Settings come from the
environment (EDFINDER_PI_*); the defaults are the Pi's, the same as
make_api_env.py's. Standard library only; needs Python 3.9 or newer.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional, Sequence
from urllib.parse import quote

DEFAULTS: Dict[str, str] = {
    "EDFINDER_PI_EDDN_ENV": "/etc/edfinder/eddn.env",
    "EDFINDER_PI_DB_PASSWORD_FILE": "/etc/edfinder/postgres-password",
    # The database as a container reaches it over the "edfinder" Docker network.
    "EDFINDER_PI_DB_HOST": "edfinder-v3-phase4c-full-20260827_r5-postgres",
    "EDFINDER_PI_DB_USER": "edfinder_v3",
    "EDFINDER_PI_DB_NAME": "edfinder_v3_phase4c_full_20260827_r5",
}
SETTING = "EDDN_V3_DATABASE_URL"


class Refused(Exception):
    """A plain message for the person running it."""


def database_url(values: Dict[str, str], password: str) -> str:
    return "postgresql://%s:%s@%s:5432/%s" % (
        quote(values["EDFINDER_PI_DB_USER"], safe=""), quote(password, safe=""),
        values["EDFINDER_PI_DB_HOST"], quote(values["EDFINDER_PI_DB_NAME"], safe=""))


def write_private(path: Path, text: str) -> None:
    """Write the file so that only its owner can ever read it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, partial = tempfile.mkstemp(prefix=".eddn.env.", dir=str(path.parent))
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


def main(argv: Optional[Sequence[str]] = None, environ: Optional[Dict[str, str]] = None) -> int:
    source = os.environ if environ is None else environ
    values = {key: source.get(key, default) for key, default in DEFAULTS.items()}
    try:
        password_file = Path(values["EDFINDER_PI_DB_PASSWORD_FILE"])
        try:
            password = password_file.read_text(encoding="utf-8").rstrip("\r\n")
        except PermissionError:
            raise Refused("%s can only be read by root. Run this with sudo." % password_file) from None
        except FileNotFoundError:
            raise Refused("%s does not exist: is PostgreSQL set up on this machine?" % password_file) from None
        if not password:
            raise Refused("%s is empty." % password_file)
        target = Path(values["EDFINDER_PI_EDDN_ENV"])
        write_private(target, "%s=%s\n" % (SETTING, database_url(values, password)))
    except Refused as error:
        print("STOPPED: %s" % error, file=sys.stderr)
        return 1
    print("Wrote %s (readable by %s only): 1 setting, %s." % (
        target, "root" if os.geteuid() == 0 else "this user", SETTING))
    return 0


if __name__ == "__main__":
    sys.exit(main())
