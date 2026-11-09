"""V3 Frontier OAuth identity login, linking, sessions, and owner bootstrap."""
from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from edfinder_api.auth import (
    AuthenticatedUser,
    delete_session_cookie,
    get_request_authentication,
    get_request_user,
    new_session_token,
    require_same_origin,
    set_session_cookie,
    token_digest,
    write_security_audit_event,
)
from edfinder_api.config import limiter, settings, trusted_client_address
from edfinder_api.deps import get_pool, secrets_equal
from edfinder_api.journal.commanders import (
    associate_verified_commander,
    fid_from_customer_id,
    verified_commanders,
)

router = APIRouter(prefix='/api/v1/auth', tags=['auth'])
# Frontier already has this exact callback registered for the live V2 app.
# Keep only this compatibility alias; the permanent V3 contract is above.
frontier_callback_compat_router = APIRouter(prefix='/api/auth', tags=['auth'])

FRONTIER_PROVIDER = 'frontier'
FRONTIER_ISSUER = 'https://auth.frontierstore.net'


class AuthUserResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    account_id: uuid.UUID
    commander_name: Optional[str] = None
    is_owner: bool


class AuthSessionResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    authenticated: bool
    user: Optional[AuthUserResponse] = None
    owner_claim_available: bool = False


class ExternalIdentityResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    external_identity_id: uuid.UUID
    provider: str
    linked_at: datetime


class VerifiedCommanderResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    commander_id: uuid.UUID
    commander_name: str
    journal_fid: str
    verified_at: datetime


class OwnerClaimRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    admin_token: str = Field(min_length=1, max_length=512)


class FrontierIdentity(BaseModel):
    model_config = ConfigDict(extra='forbid')

    issuer: str
    subject: str
    commander_name: Optional[str] = None
    journal_fid: Optional[str] = Field(default=None, pattern=r'^F[1-9][0-9]{0,19}$')


class FrontierLinkResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    authorization_url: str


def _frontier_ready() -> bool:
    return bool(settings.frontier_client_id and settings.frontier_client_secret)


def _require_frontier_ready() -> None:
    if not _frontier_ready():
        raise HTTPException(503, 'Frontier sign-in is not configured')


def _base64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _safe_return_to(value: Optional[str]) -> str:
    candidate = (value or '/').strip()
    parsed = urlsplit(candidate)
    if (
        not candidate.startswith('/')
        or candidate.startswith('//')
        or parsed.scheme
        or parsed.netloc
        or '\r' in candidate
        or '\n' in candidate
        or '\\' in candidate
    ):
        return '/'
    return candidate


# Kept as a name for the OAuth limits and their tests; it is now the same
# trusted key every rate limit uses.
_oauth_client_address = trusted_client_address


def build_frontier_authorize_url(*, state: str, code_challenge: str) -> str:
    query = urlencode({
        'response_type': 'code',
        'client_id': settings.frontier_client_id or '',
        'redirect_uri': settings.frontier_redirect_uri,
        # Identity login requests CAPI so the callback can read the real
        # in-game commander name from companion /profile. The companion token
        # is used once for the name and never persisted.
        'scope': 'auth capi',
        'audience': 'all',
        'state': state,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
    })
    return f"{settings.frontier_auth_base_url.rstrip('/')}/auth?{query}"


def identity_from_frontier_payloads(
    decoded: dict[str, Any],
    account: dict[str, Any],
    profile: Optional[dict[str, Any]] = None,
) -> FrontierIdentity:
    """Adapt the PR #490 issuer and stable parent-customer identity logic."""
    issuer = str(decoded.get('iss') or '').rstrip('/')
    expected_issuer = settings.frontier_auth_base_url.rstrip('/')
    if issuer != expected_issuer or issuer != FRONTIER_ISSUER:
        raise HTTPException(502, 'Frontier returned an unexpected token issuer')

    decoded_user = decoded.get('usr')
    if not isinstance(decoded_user, dict):
        raise HTTPException(502, 'Frontier token did not contain an account identity')

    decoded_id = str(decoded_user.get('customer_id') or '').strip()
    account_id = str(account.get('customer_id') or '').strip()
    parent_id = str(account.get('parent_id') or '').strip()
    if decoded_id and account_id and decoded_id != account_id:
        raise HTTPException(502, 'Frontier returned conflicting account identities')
    subject = parent_id or account_id or decoded_id
    if not subject:
        raise HTTPException(502, 'Frontier account identity was empty')

    # Identity login now requests scope 'auth capi' and calls
    # companion.orerve.net/profile fail-open (see _exchange_frontier_code).
    # The CAPI access token is used only for that one request and is then
    # discarded: it is never persisted, returned to callers, or logged.
    commander_name: Optional[str] = None
    if isinstance(profile, dict):
        commander = profile.get('commander')
        if isinstance(commander, dict):
            raw_name = commander.get('name')
            if isinstance(raw_name, str) and raw_name.strip():
                commander_name = raw_name.strip()[:128]

    return FrontierIdentity(
        issuer=issuer,
        subject=subject,
        commander_name=commander_name,
        # Preserve the token's actual customer identity before parent-account
        # grouping. A parent_id or display name cannot prove journal ownership.
        journal_fid=fid_from_customer_id(decoded_user.get('customer_id')),
    )


async def _exchange_frontier_code(code: str, verifier: str) -> dict[str, Any]:
    headers = {
        'Accept': 'application/json',
        'User-Agent': settings.frontier_user_agent,
    }
    async with httpx.AsyncClient(headers=headers, timeout=12.0) as client:
        try:
            token_response = await client.post(
                f"{settings.frontier_auth_base_url.rstrip('/')}/token",
                data={
                    'grant_type': 'authorization_code',
                    'client_id': settings.frontier_client_id,
                    'client_secret': settings.frontier_client_secret,
                    'code': code,
                    'code_verifier': verifier,
                    'redirect_uri': settings.frontier_redirect_uri,
                },
            )
            token_response.raise_for_status()
            token_payload = token_response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, 'Frontier token exchange failed') from exc

        if not isinstance(token_payload, dict):
            raise HTTPException(
                502,
                'Frontier token exchange returned an invalid response',
            )
        access_token = token_payload.get('access_token')
        token_type = token_payload.get('token_type', 'Bearer')
        if not isinstance(access_token, str) or not access_token:
            raise HTTPException(502, 'Frontier token exchange returned no access token')
        if not isinstance(token_type, str) or token_type.casefold() != 'bearer':
            raise HTTPException(502, 'Frontier returned an invalid token type')

        auth_headers = {'Authorization': f'{token_type} {access_token}'}
        try:
            decode_response = await client.get(
                f"{settings.frontier_auth_base_url.rstrip('/')}/decode",
                headers=auth_headers,
            )
            decode_response.raise_for_status()
            decoded = decode_response.json()

            account_response = await client.get(
                f"{settings.frontier_auth_base_url.rstrip('/')}/me",
                headers=auth_headers,
            )
            account_response.raise_for_status()
            account = account_response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, 'Frontier account lookup failed') from exc

    if not isinstance(decoded, dict) or not isinstance(account, dict):
        raise HTTPException(502, 'Frontier returned an invalid account response')
    # access_token and any refresh_token in token_payload leave scope here and
    # are never returned to callers or written to PostgreSQL.

    profile: Optional[dict[str, Any]] = None
    try:
        # Short timeout: the commander name is best-effort and login is
        # load-bearing, so a slow/hung CAPI must not stall sign-in. On timeout
        # the except below fails open with no name.
        async with httpx.AsyncClient(headers=headers, timeout=5.0) as capi:
            profile_response = await capi.get(
                f"{settings.frontier_capi_base_url.rstrip('/')}/profile",
                headers=auth_headers,
            )
            if profile_response.status_code == 200:
                parsed = profile_response.json()
                if isinstance(parsed, dict):
                    profile = parsed
            # 204/4xx/5xx: fail open — login proceeds with no CAPI name.
    except (httpx.HTTPError, ValueError):
        profile = None

    return identity_from_frontier_payloads(decoded, account, profile).model_dump()


async def _consume_login_state(
    pool: asyncpg.Pool,
    state: str,
) -> Optional[asyncpg.Record]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                'DELETE FROM v3_identity.oauth_login_state WHERE expires_at <= NOW()'
            )
            return await conn.fetchrow(
                """
                DELETE FROM v3_identity.oauth_login_state
                WHERE state_sha256 = $1
                  AND expires_at > NOW()
                RETURNING code_verifier, return_to, intent, account_id
                """,
                token_digest(state),
            )


async def _create_login_state(
    pool: asyncpg.Pool,
    *,
    return_to: str,
    intent: str,
    account_id: Optional[uuid.UUID] = None,
) -> tuple[str, str]:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = _base64url(hashlib.sha256(verifier.encode('ascii')).digest())
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=settings.auth_state_ttl_seconds,
    )
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                'DELETE FROM v3_identity.oauth_login_state WHERE expires_at <= NOW()'
            )
            await conn.execute(
                """
                INSERT INTO v3_identity.oauth_login_state (
                    state_sha256,
                    code_verifier,
                    return_to,
                    intent,
                    account_id,
                    expires_at
                )
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                token_digest(state),
                verifier,
                _safe_return_to(return_to),
                intent.upper(),
                account_id,
                expires_at,
            )
    return state, challenge


async def _upsert_account_and_session(
    pool: asyncpg.Pool,
    identity: FrontierIdentity,
    *,
    link_to_account_id: Optional[uuid.UUID] = None,
) -> tuple[AuthenticatedUser, str, datetime]:
    raw_session = new_session_token()
    now = datetime.now(timezone.utc)
    absolute_expires_at = now + timedelta(
        seconds=settings.auth_session_absolute_ttl_seconds,
    )
    idle_expires_at = min(
        absolute_expires_at,
        now + timedelta(seconds=settings.auth_session_idle_ttl_seconds),
    )
    recent_auth_expires_at = min(
        absolute_expires_at,
        now + timedelta(seconds=settings.auth_recent_auth_ttl_seconds),
    )
    session_id = uuid.uuid4()
    identity_lock = token_digest(
        f'{FRONTIER_PROVIDER}\0{identity.issuer}\0{identity.subject}'
    ).hex()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                identity_lock,
            )
            existing_identity = await conn.fetchrow(
                """
                SELECT account_id, disabled_at
                FROM v3_identity.external_identity
                WHERE provider = $1 AND issuer = $2 AND subject = $3
                FOR UPDATE
                """,
                FRONTIER_PROVIDER,
                identity.issuer,
                identity.subject,
            )
            existing_account_id = (
                existing_identity['account_id']
                if existing_identity is not None
                else None
            )

            if link_to_account_id is not None:
                target_exists = await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM v3_identity.account
                        WHERE account_id = $1 AND account_state = 'ACTIVE'
                    )
                    """,
                    link_to_account_id,
                )
                if not target_exists:
                    raise HTTPException(401, 'Linking account is no longer active')
                if (
                    existing_account_id is not None
                    and uuid.UUID(str(existing_account_id)) != link_to_account_id
                ):
                    raise HTTPException(
                        409,
                        'Frontier identity is already linked to another account',
                    )
                account_id = link_to_account_id
            elif existing_account_id is not None:
                if existing_identity['disabled_at'] is not None:
                    raise HTTPException(
                        403,
                        'Frontier identity is no longer linked',
                    )
                account_id = uuid.UUID(str(existing_account_id))
                account_active = await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM v3_identity.account
                        WHERE account_id = $1 AND account_state = 'ACTIVE'
                    )
                    """,
                    account_id,
                )
                if not account_active:
                    raise HTTPException(403, 'ED-Finder account is not active')
            else:
                account_id = uuid.uuid4()
                await conn.execute(
                    """
                    INSERT INTO v3_identity.account (account_id)
                    VALUES ($1)
                    """,
                    account_id,
                )

            if existing_account_id is None:
                await conn.execute(
                    """
                    INSERT INTO v3_identity.external_identity (
                        external_identity_id,
                        account_id,
                        provider,
                        issuer,
                        subject,
                        verified_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    uuid.uuid4(),
                    account_id,
                    FRONTIER_PROVIDER,
                    identity.issuer,
                    identity.subject,
                    now,
                )
            else:
                await conn.execute(
                    """
                    UPDATE v3_identity.external_identity
                    SET
                        verified_at = $4,
                        disabled_at = CASE WHEN $5 THEN NULL ELSE disabled_at END
                    WHERE provider = $1 AND issuer = $2 AND subject = $3
                    """,
                    FRONTIER_PROVIDER,
                    identity.issuer,
                    identity.subject,
                    now,
                    link_to_account_id is not None,
                )

            if identity.journal_fid:
                await associate_verified_commander(
                    conn, account_id=account_id, issuer=identity.issuer,
                    fid=identity.journal_fid, verified_at=now,
                    commander_name=identity.commander_name,
                )
            elif identity.commander_name:
                commander_id = await conn.fetchval(
                    """
                    SELECT commander.commander_id
                    FROM v3_identity.account_commander_access AS access
                    JOIN v3_identity.commander AS commander
                      ON commander.commander_id = access.commander_id
                    WHERE access.account_id = $1
                      AND access.revoked_at IS NULL
                      AND commander.commander_state = 'ACTIVE'
                    ORDER BY
                        CASE access.access_role WHEN 'OWNER' THEN 0 ELSE 1 END,
                        access.granted_at,
                        commander.commander_id
                    LIMIT 1
                    FOR UPDATE OF commander
                    """,
                    account_id,
                )
                if commander_id is None:
                    commander_id = uuid.uuid4()
                    await conn.execute(
                        """
                        INSERT INTO v3_identity.commander (
                            commander_id,
                            commander_name
                        )
                        VALUES ($1, $2)
                        """,
                        commander_id,
                        identity.commander_name,
                    )
                    await conn.execute(
                        """
                        INSERT INTO v3_identity.account_commander_access (
                            account_id,
                            commander_id,
                            access_role
                        )
                        VALUES ($1, $2, 'OWNER')
                        """,
                        account_id,
                        commander_id,
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE v3_identity.commander
                        SET commander_name = $2, updated_at = $3
                        WHERE commander_id = $1
                        """,
                        commander_id,
                        identity.commander_name,
                        now,
                    )

            if identity.subject in settings.frontier_owner_ids:
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext('ed-finder-owner-claim'))"
                )
                owner_account_id = await conn.fetchval(
                    """
                    SELECT account_role.account_id
                    FROM v3_identity.account_role AS account_role
                    JOIN v3_identity.role AS role
                      ON role.role_id = account_role.role_id
                    WHERE role.public_code = 'OWNER'
                      AND account_role.revoked_at IS NULL
                    """,
                )
                if owner_account_id is None or uuid.UUID(str(owner_account_id)) == account_id:
                    await conn.execute(
                        """
                        INSERT INTO v3_identity.account_role (
                            account_id,
                            role_id,
                            grant_source
                        )
                        SELECT $1, role_id, 'frontier_owner_allowlist'
                        FROM v3_identity.role
                        WHERE public_code = 'OWNER'
                        ON CONFLICT (account_id, role_id) DO NOTHING
                        """,
                        account_id,
                    )

            await conn.execute(
                """
                INSERT INTO v3_identity.session (
                    session_id,
                    account_id,
                    session_token_sha256,
                    session_state,
                    created_at,
                    last_seen_at,
                    idle_expires_at,
                    absolute_expires_at,
                    last_authenticated_at,
                    recent_auth_expires_at,
                    rotation_counter
                )
                VALUES ($1, $2, $3, 'ACTIVE', $4, $4, $5, $6, $4, $7, 0)
                """,
                session_id,
                account_id,
                token_digest(raw_session),
                now,
                idle_expires_at,
                absolute_expires_at,
                recent_auth_expires_at,
            )
            await write_security_audit_event(
                conn,
                event_type=(
                    'external_identity.linked'
                    if link_to_account_id is not None
                    else 'frontier.login'
                ),
                succeeded=True,
                account_id=account_id,
                metadata={'provider': FRONTIER_PROVIDER, 'issuer': identity.issuer},
            )
            record = await conn.fetchrow(
                """
                SELECT
                    $2::uuid AS session_id,
                    account.account_id,
                    $3::timestamptz AS recent_auth_at,
                    commander.commander_name,
                    EXISTS (
                        SELECT 1
                        FROM v3_identity.account_role AS account_role
                        JOIN v3_identity.role AS role
                          ON role.role_id = account_role.role_id
                        WHERE account_role.account_id = account.account_id
                          AND account_role.revoked_at IS NULL
                          AND role.public_code = 'OWNER'
                    ) AS is_owner
                FROM v3_identity.account AS account
                LEFT JOIN LATERAL (
                    SELECT commander.commander_name
                    FROM v3_identity.account_commander_access AS access
                    JOIN v3_identity.commander AS commander
                      ON commander.commander_id = access.commander_id
                    WHERE access.account_id = account.account_id
                      AND access.revoked_at IS NULL
                      AND commander.commander_state = 'ACTIVE'
                    ORDER BY
                        CASE access.access_role WHEN 'OWNER' THEN 0 ELSE 1 END,
                        access.granted_at,
                        commander.commander_id
                    LIMIT 1
                ) AS commander ON TRUE
                WHERE account.account_id = $1
                """,
                account_id,
                session_id,
                now,
            )

    user = AuthenticatedUser(
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
    return user, raw_session, absolute_expires_at


async def _owner_claim_available(
    pool: asyncpg.Pool,
    user: AuthenticatedUser,
) -> bool:
    if user.is_owner or not settings.admin_token or settings.frontier_owner_ids:
        return False
    async with pool.acquire() as conn:
        owner_exists = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1
                FROM v3_identity.account_role AS account_role
                JOIN v3_identity.role AS role
                  ON role.role_id = account_role.role_id
                WHERE role.public_code = 'OWNER'
                  AND account_role.revoked_at IS NULL
            )
            """,
        )
    return not bool(owner_exists)


def _session_response(
    user: Optional[AuthenticatedUser],
    *,
    owner_claim_available: bool = False,
) -> AuthSessionResponse:
    if user is None:
        return AuthSessionResponse(authenticated=False)
    return AuthSessionResponse(
        authenticated=True,
        user=AuthUserResponse(
            account_id=user.account_id,
            commander_name=user.commander_name,
            is_owner=user.is_owner,
        ),
        owner_claim_available=owner_claim_available,
    )


def _canonical_frontier_login_url(request: Request) -> Optional[str]:
    """Keep the OAuth state cookie on the registered callback host."""
    callback = urlsplit(settings.frontier_redirect_uri)
    if callback.scheme not in {'http', 'https'} or not callback.netloc:
        return None
    if request.url.netloc.casefold() == callback.netloc.casefold():
        return None
    query = f'?{request.url.query}' if request.url.query else ''
    return f'{callback.scheme}://{callback.netloc}{request.url.path}{query}'


def _set_state_cookie(response: Response, state: str) -> None:
    response.set_cookie(
        settings.auth_state_cookie_name,
        state,
        max_age=settings.auth_state_ttl_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite='lax',
        path='/api',
    )


def _delete_state_cookie(response: Response) -> None:
    response.delete_cookie(
        settings.auth_state_cookie_name,
        path='/api',
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite='lax',
    )


@router.get('/frontier/login')
@limiter.limit('20/minute', key_func=_oauth_client_address)
async def frontier_login(
    request: Request,
    return_to: Optional[str] = None,
    pool: asyncpg.Pool = Depends(get_pool),
):
    _require_frontier_ready()
    canonical_url = _canonical_frontier_login_url(request)
    if canonical_url is not None:
        return RedirectResponse(canonical_url, status_code=307)

    state, challenge = await _create_login_state(
        pool,
        return_to=_safe_return_to(return_to),
        intent='login',
    )
    response = RedirectResponse(
        build_frontier_authorize_url(state=state, code_challenge=challenge),
        status_code=302,
    )
    _set_state_cookie(response, state)
    return response


@router.post('/frontier/link', response_model=FrontierLinkResponse)
@limiter.limit('5/minute')
async def frontier_link(
    request: Request,
    return_to: Optional[str] = None,
    pool: asyncpg.Pool = Depends(get_pool),
):
    _require_frontier_ready()
    require_same_origin(request)
    user = await get_request_user(request)
    if user is None:
        raise HTTPException(401, 'Sign in before linking another identity')
    if not user.recently_authenticated:
        raise HTTPException(403, 'Recent authentication required')

    state, challenge = await _create_login_state(
        pool,
        return_to=_safe_return_to(return_to),
        intent='link',
        account_id=user.account_id,
    )
    response = JSONResponse({
        'authorization_url': build_frontier_authorize_url(
            state=state,
            code_challenge=challenge,
        ),
    })
    _set_state_cookie(response, state)
    return response


@frontier_callback_compat_router.get('/frontier/callback', include_in_schema=False)
@router.get('/frontier/callback')
@limiter.limit('20/minute', key_func=_oauth_client_address)
async def frontier_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    pool: asyncpg.Pool = Depends(get_pool),
):
    _require_frontier_ready()
    cookie_state = request.cookies.get(settings.auth_state_cookie_name, '')
    if not state or not cookie_state or not secrets_equal(state, cookie_state):
        raise HTTPException(400, 'Frontier sign-in state did not match')

    stored = await _consume_login_state(pool, state)
    if stored is None:
        raise HTTPException(400, 'Frontier sign-in state expired or was already used')
    return_to = _safe_return_to(str(stored['return_to']))

    if error or not code:
        destination = urlsplit(return_to)
        query = [(key, value) for key, value in parse_qsl(destination.query, keep_blank_values=True)
                 if key != 'auth']
        query.append(('auth', 'denied'))
        response = RedirectResponse(
            urlunsplit(destination._replace(query=urlencode(query))), status_code=302,
        )
        _delete_state_cookie(response)
        return response

    identity_payload = await _exchange_frontier_code(code, str(stored['code_verifier']))
    link_to_account_id = (
        uuid.UUID(str(stored['account_id']))
        if stored['intent'] == 'LINK' and stored['account_id'] is not None
        else None
    )
    user, raw_session, absolute_expires_at = await _upsert_account_and_session(
        pool,
        FrontierIdentity.model_validate(identity_payload),
        link_to_account_id=link_to_account_id,
    )

    response = RedirectResponse(return_to, status_code=302)
    set_session_cookie(
        response,
        raw_session,
        absolute_expires_at=absolute_expires_at,
    )
    _delete_state_cookie(response)
    return response


@router.get('/session', response_model=AuthSessionResponse)
async def auth_session(
    request: Request,
    response: Response,
    pool: asyncpg.Pool = Depends(get_pool),
):
    authentication = await get_request_authentication(
        request,
        rotate=True,
        pool=pool,
    )
    if authentication is None:
        if request.cookies.get(settings.auth_session_cookie_name):
            delete_session_cookie(response)
        return _session_response(None)

    if authentication.rotated_token:
        set_session_cookie(
            response,
            authentication.rotated_token,
            absolute_expires_at=authentication.absolute_expires_at,
        )
    claim_available = await _owner_claim_available(pool, authentication.user)
    return _session_response(
        authentication.user,
        owner_claim_available=claim_available,
    )


@router.post('/logout', response_model=AuthSessionResponse)
async def auth_logout(
    request: Request,
    response: Response,
    pool: asyncpg.Pool = Depends(get_pool),
):
    require_same_origin(request)
    raw_session = request.cookies.get(settings.auth_session_cookie_name, '')
    if raw_session:
        async with pool.acquire() as conn:
            async with conn.transaction():
                account_id = await conn.fetchval(
                    """
                    UPDATE v3_identity.session
                    SET
                        session_state = 'REVOKED',
                        revoked_at = NOW(),
                        revocation_reason = 'logout'
                    WHERE session_token_sha256 = $1
                      AND session_state = 'ACTIVE'
                    RETURNING account_id
                    """,
                    token_digest(raw_session),
                )
                if account_id is not None:
                    await write_security_audit_event(
                        conn,
                        event_type='session.logout',
                        succeeded=True,
                        account_id=uuid.UUID(str(account_id)),
                    )
    delete_session_cookie(response)
    return _session_response(None)


@router.get('/commanders', response_model=list[VerifiedCommanderResponse],
            operation_id='listVerifiedCommanders')
async def list_verified_commanders(
    request: Request, pool: asyncpg.Pool = Depends(get_pool),
):
    user = await get_request_user(request)
    if user is None:
        raise HTTPException(401, 'Sign in before viewing your commanders')
    async with pool.acquire() as conn:
        return await verified_commanders(conn, user.account_id)


@router.get('/identities', response_model=list[ExternalIdentityResponse])
async def list_identities(
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
):
    """List the active login identities owned by the current account.

    Provider subjects and other Frontier identifiers stay server-side. The
    browser only needs an opaque row id to render safe unlink controls.
    """
    user = await get_request_user(request)
    if user is None:
        raise HTTPException(401, 'Sign in before viewing linked identities')

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT external_identity_id, provider, verified_at AS linked_at
            FROM v3_identity.external_identity
            WHERE account_id = $1 AND disabled_at IS NULL
            ORDER BY verified_at, external_identity_id
            """,
            user.account_id,
        )
    return [ExternalIdentityResponse.model_validate(dict(row)) for row in rows]


@router.post('/owner/claim', response_model=AuthSessionResponse)
@limiter.limit('5/minute')
async def claim_owner(
    request: Request,
    payload: OwnerClaimRequest,
    pool: asyncpg.Pool = Depends(get_pool),
):
    require_same_origin(request)
    user = await get_request_user(request)
    if user is None:
        raise HTTPException(401, 'Sign in with Frontier before linking the owner account')
    if user.is_owner:
        return _session_response(user)
    if not user.recently_authenticated:
        raise HTTPException(403, 'Recent authentication required')
    if not settings.admin_token or not secrets_equal(
        payload.admin_token,
        settings.admin_token,
    ):
        raise HTTPException(401, 'Invalid admin token')

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext('ed-finder-owner-claim'))"
            )
            owner_exists = await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1
                    FROM v3_identity.account_role AS account_role
                    JOIN v3_identity.role AS role
                      ON role.role_id = account_role.role_id
                    WHERE role.public_code = 'OWNER'
                      AND account_role.revoked_at IS NULL
                )
                """,
            )
            if owner_exists:
                raise HTTPException(409, 'An owner account is already linked')
            await conn.execute(
                """
                INSERT INTO v3_identity.account_role (
                    account_id,
                    role_id,
                    granted_by_account_id,
                    grant_source
                )
                SELECT
                    $1,
                    role_id,
                    $1,
                    'one_time_admin_token_bootstrap'
                FROM v3_identity.role
                WHERE public_code = 'OWNER'
                """,
                user.account_id,
            )
            await write_security_audit_event(
                conn,
                event_type='owner.bootstrap_claimed',
                succeeded=True,
                account_id=user.account_id,
            )
    return _session_response(replace(user, is_owner=True))


@router.delete('/identities/{external_identity_id}', response_model=AuthSessionResponse)
@limiter.limit('5/minute')
async def unlink_identity(
    external_identity_id: uuid.UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
):
    require_same_origin(request)
    user = await get_request_user(request)
    if user is None:
        raise HTTPException(401, 'Sign in before unlinking an identity')
    if not user.recently_authenticated:
        raise HTTPException(403, 'Recent authentication required')

    async with pool.acquire() as conn:
        async with conn.transaction():
            identity = await conn.fetchrow(
                """
                SELECT external_identity_id, provider
                FROM v3_identity.external_identity
                WHERE external_identity_id = $1
                  AND account_id = $2
                  AND disabled_at IS NULL
                FOR UPDATE
                """,
                external_identity_id,
                user.account_id,
            )
            if identity is None:
                raise HTTPException(404, 'Linked identity not found')
            identity_count = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM v3_identity.external_identity
                WHERE account_id = $1 AND disabled_at IS NULL
                """,
                user.account_id,
            )
            if int(identity_count) <= 1:
                raise HTTPException(409, 'Cannot unlink the last login identity')
            await conn.execute(
                """
                UPDATE v3_identity.external_identity
                SET disabled_at = NOW()
                WHERE external_identity_id = $1
                """,
                external_identity_id,
            )
            await write_security_audit_event(
                conn,
                event_type='external_identity.unlinked',
                succeeded=True,
                account_id=user.account_id,
                metadata={'provider': str(identity['provider'])},
            )
    return _session_response(user)
