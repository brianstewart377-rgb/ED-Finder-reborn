"""V3 account authentication and opaque rotating web-session helpers."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import asyncpg
from fastapi import HTTPException, Request, Response

from edfinder_api.config import settings
from edfinder_api.state import get_pool_singleton


@dataclass(frozen=True)
class AuthenticatedUser:
    """Authenticated ED-Finder account; provider subject is not the app ID."""

    account_id: uuid.UUID
    commander_name: Optional[str]
    is_owner: bool
    recent_auth_at: datetime
    session_id: uuid.UUID

    @property
    def recently_authenticated(self) -> bool:
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=settings.auth_recent_auth_ttl_seconds,
        )
        return self.recent_auth_at >= cutoff


@dataclass(frozen=True)
class SessionAuthentication:
    user: AuthenticatedUser
    rotated_token: Optional[str]
    absolute_expires_at: datetime


def token_digest(token: str) -> bytes:
    """Return the 32-byte digest stored by the frozen V3 baseline."""
    return hashlib.sha256(token.encode('utf-8')).digest()


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def user_from_record(record: asyncpg.Record) -> AuthenticatedUser:
    return AuthenticatedUser(
        account_id=uuid.UUID(str(record['account_id'])),
        commander_name=(
            str(record['commander_name'])
            if record['commander_name'] is not None
            else None
        ),
        is_owner=bool(record['is_owner']),
        recent_auth_at=record['recent_auth_at'],
        session_id=uuid.UUID(str(record['session_id'])),
    )


async def write_security_audit_event(
    conn: asyncpg.Connection,
    *,
    event_type: str,
    succeeded: bool,
    account_id: Optional[uuid.UUID] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Append a metadata-minimised security event inside the caller's tx."""
    await conn.execute(
        """
        INSERT INTO v3_identity.security_audit_event (
            account_id,
            event_code,
            outcome,
            detail
        )
        VALUES ($1, $2, $3, $4)
        """,
        account_id,
        event_type,
        'SUCCEEDED' if succeeded else 'FAILED',
        metadata or {},
    )


async def load_session(
    pool: asyncpg.Pool,
    raw_token: str,
    *,
    rotate: bool = False,
) -> Optional[SessionAuthentication]:
    """Load, touch, and optionally rotate one valid opaque session."""
    if not raw_token:
        return None

    now = datetime.now(timezone.utc)
    rotation_cutoff = now - timedelta(seconds=settings.auth_session_rotation_seconds)
    async with pool.acquire() as conn:
        async with conn.transaction():
            record = await conn.fetchrow(
                """
                SELECT
                    sessions.session_id,
                    sessions.account_id,
                    sessions.last_authenticated_at AS recent_auth_at,
                    sessions.created_at AS lineage_created_at,
                    sessions.token_issued_at AS rotation_anchor,
                    sessions.recent_auth_expires_at,
                    sessions.absolute_expires_at,
                    commander.commander_name,
                    EXISTS (
                        SELECT 1
                        FROM v3_identity.account_role AS account_role
                        JOIN v3_identity.role AS role
                          ON role.role_id = account_role.role_id
                        WHERE account_role.account_id = sessions.account_id
                          AND account_role.revoked_at IS NULL
                          AND role.public_code = 'OWNER'
                    ) AS is_owner
                FROM v3_identity.session AS sessions
                JOIN v3_identity.account AS account
                  ON account.account_id = sessions.account_id
                 AND account.account_state = 'ACTIVE'
                LEFT JOIN LATERAL (
                    SELECT commander.commander_name
                    FROM v3_identity.account_commander_access AS access
                    JOIN v3_identity.commander AS commander
                      ON commander.commander_id = access.commander_id
                    WHERE access.account_id = sessions.account_id
                      AND access.revoked_at IS NULL
                      AND commander.commander_state = 'ACTIVE'
                    ORDER BY
                        CASE access.access_role WHEN 'OWNER' THEN 0 ELSE 1 END,
                        access.granted_at,
                        commander.commander_id
                    LIMIT 1
                ) AS commander ON TRUE
                WHERE sessions.session_token_sha256 = $1
                  AND sessions.session_state = 'ACTIVE'
                  AND sessions.idle_expires_at > $2
                  AND sessions.absolute_expires_at > $2
                FOR UPDATE OF sessions
                """,
                token_digest(raw_token),
                now,
            )
            if record is None:
                return None

            absolute_expires_at = record['absolute_expires_at']
            idle_expires_at = min(
                absolute_expires_at,
                now + timedelta(seconds=settings.auth_session_idle_ttl_seconds),
            )
            rotated_token: Optional[str] = None

            if rotate and record['rotation_anchor'] <= rotation_cutoff:
                rotated_token = new_session_token()
                new_session_id = uuid.uuid4()
                await conn.execute(
                    """
                    INSERT INTO v3_identity.session (
                        session_id,
                        account_id,
                        session_token_sha256,
                        session_state,
                        created_at,
                        token_issued_at,
                        last_seen_at,
                        idle_expires_at,
                        absolute_expires_at,
                        last_authenticated_at,
                        recent_auth_expires_at,
                        rotation_counter
                    )
                    VALUES ($1, $2, $3, 'ACTIVE', $4, $5, $5, $6, $7, $8, $9, 0)
                    """,
                    new_session_id,
                    record['account_id'],
                    token_digest(rotated_token),
                    record['lineage_created_at'],
                    now,
                    idle_expires_at,
                    absolute_expires_at,
                    record['recent_auth_at'],
                    record['recent_auth_expires_at'],
                )
                await conn.execute(
                    """
                    UPDATE v3_identity.session
                    SET
                        last_seen_at = $2,
                        session_state = 'ROTATED',
                        revocation_reason = 'rotated',
                        rotated_to_session_id = $3,
                        rotated_at = $2,
                        rotation_counter = rotation_counter + 1
                    WHERE session_id = $1
                    """,
                    record['session_id'],
                    now,
                    new_session_id,
                )
                await write_security_audit_event(
                    conn,
                    event_type='session.rotated',
                    succeeded=True,
                    account_id=uuid.UUID(str(record['account_id'])),
                )
                mutable_record = dict(record)
                mutable_record['session_id'] = new_session_id
                record = mutable_record  # type: ignore[assignment]
            else:
                await conn.execute(
                    """
                    UPDATE v3_identity.session
                    SET last_seen_at = $2, idle_expires_at = $3
                    WHERE session_id = $1
                    """,
                    record['session_id'],
                    now,
                    idle_expires_at,
                )

    return SessionAuthentication(
        user=user_from_record(record),
        rotated_token=rotated_token,
        absolute_expires_at=absolute_expires_at,
    )


async def get_request_authentication(
    request: Request,
    *,
    rotate: bool = False,
    pool: Optional[asyncpg.Pool] = None,
) -> Optional[SessionAuthentication]:
    session_pool = pool or get_pool_singleton()
    if session_pool is None:
        return None
    raw_token = request.cookies.get(settings.auth_session_cookie_name, '')
    return await load_session(session_pool, raw_token, rotate=rotate)


async def get_request_user(request: Request) -> Optional[AuthenticatedUser]:
    authentication = await get_request_authentication(request)
    return authentication.user if authentication is not None else None


def set_session_cookie(
    response: Response,
    raw_token: str,
    *,
    absolute_expires_at: Optional[datetime] = None,
) -> None:
    max_age = settings.auth_session_absolute_ttl_seconds
    if absolute_expires_at is not None:
        remaining = int(
            (absolute_expires_at - datetime.now(timezone.utc)).total_seconds()
        )
        max_age = max(0, min(max_age, remaining))
    response.set_cookie(
        settings.auth_session_cookie_name,
        raw_token,
        max_age=max_age,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite='lax',
        path='/',
    )


def delete_session_cookie(response: Response) -> None:
    response.delete_cookie(
        settings.auth_session_cookie_name,
        path='/',
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite='lax',
    )


def require_same_origin(request: Request) -> None:
    """Reject cookie-authorized writes from untrusted browser origins."""
    if request.method.upper() in {'GET', 'HEAD', 'OPTIONS'}:
        return
    origin = request.headers.get('Origin', '').strip().rstrip('/')
    allowed = {
        value.strip().rstrip('/')
        for value in settings.cors_origins.split(',')
        if value.strip()
    }
    if not origin or origin not in allowed:
        raise HTTPException(403, 'Trusted request origin required')
