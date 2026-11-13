"""The EDDN listener's settings file for the Pi (scripts/pi/make_eddn_env.py):
one setting, readable by its owner only, and the password never shown."""
from __future__ import annotations

import importlib.util
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("make_eddn_env", ROOT / "scripts" / "pi" / "make_eddn_env.py")
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

for _path in (ROOT / "apps" / "eddn_v3" / "src",):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

PASSWORD = "p@ss/word with:odd?chars#%"


def settings(tmp_path: Path) -> dict[str, str]:
    (tmp_path / "postgres-password").write_text(PASSWORD + "\n")
    return {"EDFINDER_PI_EDDN_ENV": str(tmp_path / "etc" / "eddn.env"),
            "EDFINDER_PI_DB_PASSWORD_FILE": str(tmp_path / "postgres-password")}


def test_it_writes_one_setting_only_its_owner_can_read_and_shows_no_password(tmp_path, capsys):
    environ = settings(tmp_path)
    assert tool.main([], environ) == 0
    said = capsys.readouterr()
    target = tmp_path / "etc" / "eddn.env"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    lines = target.read_text().splitlines()
    assert len(lines) == 1 and lines[0].startswith("EDDN_V3_DATABASE_URL=postgresql://edfinder_v3:")
    assert lines[0].endswith("@edfinder-v3-phase4c-full-20260827_r5-postgres:5432/edfinder_v3_phase4c_full_20260827_r5")
    # Nothing of the password on the screen, in any form.
    for secret in (PASSWORD, "p%40ss", "word"):
        assert secret not in said.out + said.err
    assert "1 setting, EDDN_V3_DATABASE_URL" in said.out
    # No file left half-written beside it, and a second run gives the same file.
    assert [item.name for item in target.parent.iterdir()] == ["eddn.env"]
    before = target.read_text()
    assert tool.main([], environ) == 0 and target.read_text() == before


def test_the_listener_and_the_database_driver_read_what_it_wrote(tmp_path):
    """The password goes in with its odd characters escaped, and comes out whole."""
    from psycopg.conninfo import conninfo_to_dict

    from edfinder_observe.config import load_config

    environ = settings(tmp_path)
    assert tool.main([], environ) == 0
    name, _, value = (tmp_path / "etc" / "eddn.env").read_text().strip().partition("=")
    config = load_config({name: value})
    read = conninfo_to_dict(config.database_url)
    assert read["password"] == PASSWORD
    assert (read["user"], read["port"]) == ("edfinder_v3", "5432")
    assert PASSWORD not in repr(config), "the listener never prints its database address"
    # Docker Compose reads this file. A "$" would be taken for a setting's name
    # to fill in, and a space or a quote would cut the value short: none can be there.
    assert not set(value) & set("$ '\"\\`")


def test_it_stops_with_a_plain_message_when_the_password_file_is_missing_or_empty(tmp_path, capsys):
    environ = settings(tmp_path)
    (tmp_path / "postgres-password").write_text("\n")
    assert tool.main([], environ) == 1
    assert "is empty" in capsys.readouterr().err
    (tmp_path / "postgres-password").unlink()
    assert tool.main([], environ) == 1
    assert "does not exist" in capsys.readouterr().err
    assert not (tmp_path / "etc" / "eddn.env").exists()


def test_the_compose_file_gives_the_listener_only_its_own_settings():
    text = (ROOT / "deploy" / "pi" / "eddn.yml").read_text()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "/etc/edfinder/eddn.env" in code and "api.env" not in code
    assert "ports:" not in code, "no port is opened"
    assert "external: true" in code and "container_name: edfinder-eddn" in code
    assert "/eddn:main" in code
