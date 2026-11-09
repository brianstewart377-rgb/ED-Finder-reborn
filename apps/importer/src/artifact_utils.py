"""Shared helpers for deterministic JSON artifacts and integrity hashes."""
from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping


DEFAULT_INTEGRITY_KEY = 'artifact_integrity'
HASH_ALGORITHM = 'sha256'
CANONICALIZATION = (
    'json.dumps(sort_keys=True,separators=(comma,colon),ensure_ascii=True,allow_nan=False,default=str) '
    f'excluding {DEFAULT_INTEGRITY_KEY}'
)


def canonical_json(value: Any) -> str:
    """Return the stable compact JSON representation used for artifact hashes."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=True,
        allow_nan=False,
        default=str,
    )


def sha256_bytes(data: bytes | bytearray | memoryview) -> str:
    """Return the SHA-256 hex digest for byte-oriented data."""
    return hashlib.sha256(bytes(data)).hexdigest()


def sha256_text(text: str) -> str:
    """Return the SHA-256 hex digest for UTF-8 encoded text."""
    return sha256_bytes(text.encode('utf-8'))


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Return the SHA-256 hex digest for a file without loading it all at once."""
    hasher = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def artifact_integrity_sha256(
    payload: Mapping[str, Any],
    *,
    integrity_key: str = DEFAULT_INTEGRITY_KEY,
) -> str:
    """Hash an artifact payload after excluding its self-referential integrity block."""
    return sha256_text(canonical_json(_without_integrity(payload, integrity_key=integrity_key)))


def attach_artifact_integrity(
    payload: Mapping[str, Any],
    *,
    integrity_key: str = DEFAULT_INTEGRITY_KEY,
) -> dict[str, Any]:
    """Return a copy of the artifact payload with a refreshed integrity block."""
    artifact = _without_integrity(payload, integrity_key=integrity_key)
    artifact[integrity_key] = {
        'hash_algorithm': HASH_ALGORITHM,
        'canonical_json_sha256': artifact_integrity_sha256(artifact, integrity_key=integrity_key),
        'canonicalization': _canonicalization_label(integrity_key),
    }
    return artifact


def write_json_artifact(
    path: str | Path,
    payload: Mapping[str, Any],
    *,
    integrity_key: str = DEFAULT_INTEGRITY_KEY,
    chmod_mode: int | None = 0o600,
) -> dict[str, Any]:
    """Write a canonical JSON artifact with integrity metadata and return file hashes."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    artifact = attach_artifact_integrity(payload, integrity_key=integrity_key)
    text = canonical_json(artifact) + '\n'
    data = text.encode('utf-8')

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, 'O_BINARY'):
        flags |= os.O_BINARY
    mode = 0o666 if chmod_mode is None else chmod_mode
    fd = os.open(output_path, flags, mode)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            fd = -1
    finally:
        if fd != -1:
            os.close(fd)

    if chmod_mode is not None:
        output_path.chmod(chmod_mode)

    return build_artifact_record(output_path, artifact, integrity_key=integrity_key)


def build_artifact_record(
    path: str | Path,
    payload: Mapping[str, Any],
    *,
    integrity_key: str = DEFAULT_INTEGRITY_KEY,
) -> dict[str, Any]:
    """Return compact metadata for a written JSON artifact."""
    output_path = Path(path)
    return {
        'path': str(output_path),
        'file_sha256': sha256_file(output_path),
        'artifact_integrity_sha256': artifact_integrity_sha256(payload, integrity_key=integrity_key),
        'hash_algorithm': HASH_ALGORITHM,
        'integrity_key': integrity_key,
        'bytes_written': output_path.stat().st_size,
    }


def _without_integrity(payload: Mapping[str, Any], *, integrity_key: str) -> dict[str, Any]:
    artifact = deepcopy(dict(payload))
    artifact.pop(integrity_key, None)
    return artifact


def _canonicalization_label(integrity_key: str) -> str:
    if integrity_key == DEFAULT_INTEGRITY_KEY:
        return CANONICALIZATION
    return (
        'json.dumps(sort_keys=True,separators=(comma,colon),ensure_ascii=True,allow_nan=False,default=str) '
        f'excluding {integrity_key}'
    )
