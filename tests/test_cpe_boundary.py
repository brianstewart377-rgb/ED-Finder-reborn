"""Keep the pure planner isolated from application and service code."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CPE = ROOT / 'apps' / 'api' / 'src' / 'cpe'
ALLOWED_MODULES = {'edfinder_api.domain.facilities', 'edfinder_api.mechanics.cp_rules'}
FORBIDDEN_TEXT = ('asyncpg', 'fastapi', 'optimiser', 'simulation', 'routers')


def _allowed_module(module: str) -> bool:
    return (
        module.split('.')[0] in sys.stdlib_module_names
        or module == '__future__'
        or module == 'edfinder_api.cpe'
        or module.startswith('edfinder_api.cpe.')
        or module in ALLOWED_MODULES
    )


def _import_allowed(node: ast.Import | ast.ImportFrom, source: Path) -> None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            assert _allowed_module(alias.name), f'{source}: forbidden import {alias.name}'
        return
    if node.level:
        # Relative imports must stay inside the cpe package, including nested modules.
        depth = len(source.relative_to(CPE).parts) - 1
        assert node.level <= depth + 1, f'{source}: relative import escapes cpe: {ast.unparse(node)}'
        return
    module = node.module or ''
    if module in {'edfinder_api.domain', 'edfinder_api.mechanics'}:
        for alias in node.names:
            qualified = f'{module}.{alias.name}'
            assert qualified in ALLOWED_MODULES, f'{source}: forbidden import {qualified}'
    else:
        assert _allowed_module(module), f'{source}: forbidden import {module}'


@pytest.mark.parametrize('source', sorted(CPE.rglob('*.py')), ids=lambda p: p.relative_to(CPE).as_posix())
def test_cpe_stays_within_the_pure_python_boundary(source: Path) -> None:
    text = source.read_text(encoding='utf-8')
    for forbidden in FORBIDDEN_TEXT:
        assert forbidden not in text.lower(), f'{source}: forbidden reference {forbidden}'
    for node in ast.walk(ast.parse(text, filename=str(source))):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            _import_allowed(node, source)
