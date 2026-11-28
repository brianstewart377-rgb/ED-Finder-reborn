"""Keys for ED-Finder's EDMC plugin ("Haul — design", step 2a).

A member makes a key on the Account page and pastes it into the plugin, which
sends it with everything it posts. The key is shown once, when it is made;
only its SHA-256 is kept (``v3_identity.plugin_key``, migration 034). A
cancelled key is never accepted again. A member has at most
``MAX_ACTIVE_KEYS`` keys at once: one per PC is the expected use.

Until migration 034 has been applied the table is missing; ``ready`` says so
and the routes answer 503, so this build and the migration can come in either
order.

All SQL is parameterised.
"""
from __future__ import annotations

import hashlib
import secrets
import unicodedata
import uuid
from datetime import datetime
from typing import Any, Optional

PREFIX = "edfp_"
MAX_ACTIVE_KEYS = 5
LABEL_MAX = 60
# A key's "last used" time is written at most this often, so that a plugin
# sending every few seconds does not write a row each time.
LAST_USED_EVERY_SECONDS = 300


class KeyRefused(Exception):
    """A request about keys that cannot be met; the message says why, plainly."""


def new_secret() -> str:
    """A fresh key: the prefix and 32 random bytes, URL-safe."""
    return PREFIX + secrets.token_urlsafe(32)


def digest(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("utf-8")).digest()


def clean_label(label: str) -> str:
    """The member's name for a key, trimmed; refused when empty, too long or
    holding control characters."""
    text = " ".join(str(label).split())
    if not text:
        raise KeyRefused("Give the key a name, such as \"gaming PC\".")
    if len(text) > LABEL_MAX:
        raise KeyRefused("A key's name can be at most %d characters." % LABEL_MAX)
    if any(unicodedata.category(character).startswith("C") for character in text):
        raise KeyRefused("A key's name cannot hold control characters.")
    return text


async def ready(conn: Any) -> bool:
    """Whether migration 034 has made the keys table."""
    return bool(await conn.fetchval("SELECT to_regclass('v3_identity.plugin_key') IS NOT NULL"))


def _row(row: Any) -> dict:
    return {
        "plugin_key_id": row["plugin_key_id"],
        "label": row["label"],
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
        "revoked_at": row["revoked_at"],
    }


async def list_keys(conn: Any, account_id: uuid.UUID) -> list[dict]:
    """The member's keys, newest first. Never the key or its digest."""
    rows = await conn.fetch(
        """SELECT plugin_key_id, label, created_at, last_used_at, revoked_at
             FROM v3_identity.plugin_key
            WHERE account_id = $1
            ORDER BY created_at DESC, plugin_key_id""",
        account_id,
    )
    return [_row(row) for row in rows]


async def make_key(conn: Any, account_id: uuid.UUID, label: str) -> tuple[dict, str]:
    """Make a key for the member. Returns its row and the key itself, which
    the caller shows once and must not keep. Run inside a transaction."""
    name = clean_label(label)
    # One request at a time per member, so two at once cannot pass the limit.
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", "plugin-key:%s" % account_id)
    active = await conn.fetchval(
        "SELECT count(*) FROM v3_identity.plugin_key WHERE account_id = $1 AND revoked_at IS NULL",
        account_id,
    )
    if active >= MAX_ACTIVE_KEYS:
        raise KeyRefused("You already have %d keys. Cancel one you no longer use first." % MAX_ACTIVE_KEYS)
    secret = new_secret()
    row = await conn.fetchrow(
        """INSERT INTO v3_identity.plugin_key (plugin_key_id, account_id, key_sha256, label)
           VALUES ($1, $2, $3, $4)
           RETURNING plugin_key_id, label, created_at, last_used_at, revoked_at""",
        uuid.uuid4(), account_id, digest(secret), name,
    )
    return _row(row), secret


async def revoke_key(conn: Any, account_id: uuid.UUID, plugin_key_id: uuid.UUID) -> bool:
    """Cancel one of the member's own keys. False when the member has no such
    key, or it was cancelled already."""
    done = await conn.fetchval(
        """UPDATE v3_identity.plugin_key SET revoked_at = transaction_timestamp()
            WHERE plugin_key_id = $1 AND account_id = $2 AND revoked_at IS NULL
        RETURNING true""",
        plugin_key_id, account_id,
    )
    return bool(done)


async def account_for_key(conn: Any, secret: Optional[str], now: Optional[datetime] = None) -> Optional[uuid.UUID]:
    """The account a key belongs to, if the key is live and its account is
    active; None otherwise. Notes the key's use at most every few minutes."""
    if not secret or not secret.startswith(PREFIX) or len(secret) > 200:
        return None
    row = await conn.fetchrow(
        """SELECT k.plugin_key_id, k.account_id, k.last_used_at
             FROM v3_identity.plugin_key k
             JOIN v3_identity.account account USING (account_id)
            WHERE k.key_sha256 = $1 AND k.revoked_at IS NULL
              AND account.account_state = 'ACTIVE'""",
        digest(secret),
    )
    if row is None:
        return None
    await conn.execute(
        """UPDATE v3_identity.plugin_key SET last_used_at = coalesce($2, transaction_timestamp())
            WHERE plugin_key_id = $1
              AND (last_used_at IS NULL
                   OR last_used_at < coalesce($2, transaction_timestamp()) - make_interval(secs => $3))""",
        row["plugin_key_id"], now, LAST_USED_EVERY_SECONDS,
    )
    return row["account_id"]
