"""Locate the API source tree for importer scripts in every supported layout."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable


REPO_ROOT_MARKERS = ('.git', 'pyproject.toml', 'docker-compose.yml')
DEFAULT_REQUIRED_PATHS = ('edfinder_api/__init__.py',)


def _contains_required_paths(candidate: Path, required_paths: Iterable[str]) -> bool:
    return all((candidate / relative_path).is_file() for relative_path in required_paths)


def _find_repo_root_by_marker(start: Path) -> Path | None:
    current = start
    while True:
        if any((current / marker).exists() for marker in REPO_ROOT_MARKERS):
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def resolve_api_source(
    script_path: str | Path,
    *,
    required_paths: Iterable[str] = DEFAULT_REQUIRED_PATHS,
) -> Path:
    """Return ``apps/api/src`` for a flat importer image or repo checkout.

    Compose runs importer entrypoints flat under ``/app`` and mounts the API
    tree at ``/app/apps_api_src``. Host runs instead locate the repository root
    by marker, without relying on a fragile ``Path.parents[N]`` index.
    """
    script_dir = Path(script_path).resolve().parent
    required = tuple(dict.fromkeys((*DEFAULT_REQUIRED_PATHS, *required_paths)))
    checked: list[Path] = []

    compose_mount = script_dir / 'apps_api_src'
    checked.append(compose_mount)
    if _contains_required_paths(compose_mount, required):
        return compose_mount

    repo_root = _find_repo_root_by_marker(script_dir)
    if repo_root is not None:
        repo_api_source = repo_root / 'apps' / 'api' / 'src'
        checked.append(repo_api_source)
        if _contains_required_paths(repo_api_source, required):
            return repo_api_source

    checked_paths = ', '.join(str(path) for path in checked)
    required_description = ', '.join(required)
    raise RuntimeError(
        'Could not locate the ED-Finder API source tree. '
        f'Checked: {checked_paths}. Required paths: {required_description}. '
        'Run from a full repository checkout or mount apps/api/src at '
        f'{compose_mount}.'
    )


def add_api_source_to_path(
    script_path: str | Path,
    *,
    required_paths: Iterable[str] = DEFAULT_REQUIRED_PATHS,
) -> Path:
    """Resolve the API source tree and prepend it to ``sys.path`` once."""
    api_source = resolve_api_source(script_path, required_paths=required_paths)
    api_source_text = str(api_source)
    if api_source_text not in sys.path:
        sys.path.insert(0, api_source_text)
    return api_source
