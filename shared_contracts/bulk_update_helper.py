"""Fail-closed PostgreSQL replica mode for safe bulk maintenance writes.

``session_replication_role = replica`` suppresses ordinary triggers, including
PostgreSQL's referential-integrity triggers, for the supplied connection.  It
must therefore only wrap reviewed maintenance statements that do not change
foreign-key identity columns.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


_VALID_REPLICATION_ROLES = frozenset({'origin', 'replica', 'local'})


class BulkUpdateReplicaModeError(RuntimeError):
    """Replica mode could not be verified or safely restored."""


def _read_replication_role(conn: Any) -> str:
    with conn.cursor() as cur:
        cur.execute('SHOW session_replication_role')
        row = cur.fetchone()
    if not row:
        raise BulkUpdateReplicaModeError(
            'PostgreSQL did not return session_replication_role'
        )
    role = str(row[0]).lower()
    if role not in _VALID_REPLICATION_ROLES:
        raise BulkUpdateReplicaModeError(
            f'Unexpected session_replication_role value: {role!r}'
        )
    return role


def _set_and_verify_replication_role(conn: Any, role: str) -> None:
    if role not in _VALID_REPLICATION_ROLES:
        raise BulkUpdateReplicaModeError(
            f'Refusing invalid session_replication_role value: {role!r}'
        )
    with conn.cursor() as cur:
        cur.execute(f'SET session_replication_role = {role}')
        cur.execute('SHOW session_replication_role')
        row = cur.fetchone()
    actual = str(row[0]).lower() if row else None
    if actual != role:
        raise BulkUpdateReplicaModeError(
            'Could not verify session_replication_role on the supplied '
            f'connection: expected {role!r}, got {actual!r}'
        )


def _rollback_quietly(conn: Any) -> None:
    try:
        conn.rollback()
    except Exception:
        pass


def _close_quietly(conn: Any) -> None:
    try:
        conn.close()
    except Exception:
        pass


@contextmanager
def bulk_update_replica_mode(conn: Any) -> Iterator[Any]:
    """Yield *conn* only after replica mode is set and verified on it.

    The caller must commit successful writes inside the context.  On a body
    exception, the current transaction is rolled back before restoration.
    The original replication role is restored and committed in ``finally`` so
    a pooled or otherwise reused connection cannot leak replica mode.

    A setup failure never yields to the caller.  A restoration failure closes
    the connection before raising, preventing a connection whose trigger mode
    is unknown from returning to service.
    """
    if getattr(conn, 'autocommit', False):
        raise BulkUpdateReplicaModeError(
            'Refusing replica mode on an autocommit connection because '
            'failed bulk writes could not be rolled back'
        )

    original_role: str | None = None
    try:
        original_role = _read_replication_role(conn)
        _set_and_verify_replication_role(conn, 'replica')
    except Exception as exc:
        _rollback_quietly(conn)
        if original_role is not None:
            try:
                _set_and_verify_replication_role(conn, original_role)
                conn.commit()
            except Exception:
                _rollback_quietly(conn)
                _close_quietly(conn)
        raise BulkUpdateReplicaModeError(
            'Refusing bulk write because replica mode could not be set and '
            'verified on the supplied connection'
        ) from exc

    body_error: BaseException | None = None
    try:
        yield conn
    except BaseException as exc:
        body_error = exc
        _rollback_quietly(conn)
        raise
    finally:
        try:
            _set_and_verify_replication_role(conn, original_role)
            conn.commit()
        except Exception as exc:
            _rollback_quietly(conn)
            _close_quietly(conn)
            restore_error = BulkUpdateReplicaModeError(
                'Could not restore session_replication_role; the supplied '
                'connection was closed to prevent unsafe reuse'
            )
            if body_error is not None:
                raise restore_error from body_error
            raise restore_error from exc
