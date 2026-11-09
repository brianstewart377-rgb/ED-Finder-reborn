"""Natural Elite Dangerous body hierarchy sorting helpers."""
from __future__ import annotations

import re
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any, Iterable, Mapping, Optional, TypeVar


_NUMBER_RE = re.compile(r'^\d+$')
_LOWER_ALPHA_RE = re.compile(r'^[a-z]+$')
_UPPER_ALPHA_RE = re.compile(r'^[A-Z]+$')


@dataclass(frozen=True)
class BodyHierarchyKey:
    group: int
    parts: tuple[int, ...]

    def as_sort_tuple(self) -> tuple[int, tuple[int, ...]]:
        return self.group, self.parts

    def as_string(self) -> str:
        return f'{self.group}:' + '.'.join(f'{part:06d}' for part in self.parts)


T = TypeVar('T')


def natural_body_sort_key(name: Optional[str], system_name: Optional[str] = None) -> Optional[BodyHierarchyKey]:
    """Parse an Elite body name into a hierarchy key.

    Supported examples:
      Exioce -> star/root
      Exioce 4 -> planet 4
      Exioce 4 a -> moon a
      Exioce 4 a a -> nested moon a/a
      Binary System A 1 a -> body 1 a under star A

    Names that do not match this conservative grammar return ``None`` so the
    caller can keep the original order as a stable fallback.
    """
    if not name:
        return None

    suffix = _body_suffix(name.strip(), system_name)
    if suffix is None:
        return None
    if suffix == '':
        return BodyHierarchyKey(0, (0,))

    tokens = suffix.split()
    if not tokens:
        return BodyHierarchyKey(0, (0,))

    star_prefix = 0
    if _UPPER_ALPHA_RE.fullmatch(tokens[0]):
        star_prefix = _letters_value(tokens.pop(0), base='A')
        if not tokens:
            return BodyHierarchyKey(0, (star_prefix,))

    if not tokens or not _NUMBER_RE.fullmatch(tokens[0]):
        return None

    parts = [star_prefix, int(tokens.pop(0))]
    for token in tokens:
        if not _LOWER_ALPHA_RE.fullmatch(token):
            return None
        parts.append(_letters_value(token, base='a'))

    return BodyHierarchyKey(1, tuple(parts))


def natural_body_sort_key_string(name: Optional[str], system_name: Optional[str] = None) -> Optional[str]:
    key = natural_body_sort_key(name, system_name)
    return key.as_string() if key else None


def sort_bodies_by_hierarchy(
    bodies: Iterable[T],
    *,
    system_name: Optional[str] = None,
    name_getter: Optional[Callable[[T], Optional[str]]] = None,
) -> list[T]:
    """Return bodies in natural hierarchy order with stable fallback ties."""
    getter = name_getter or _default_name_getter

    def sort_key(item: tuple[int, T]) -> tuple[int, tuple[int, ...], int]:
        index, body = item
        key = natural_body_sort_key(getter(body), system_name)
        if key is None:
            return (2, (), index)
        group, parts = key.as_sort_tuple()
        return (group, parts, index)

    return [body for _, body in sorted(enumerate(bodies), key=sort_key)]


def _body_suffix(name: str, system_name: Optional[str]) -> Optional[str]:
    system = (system_name or '').strip()
    if system:
        if name == system:
            return ''
        prefix = f'{system} '
        if name.startswith(prefix):
            return name[len(prefix):].strip()
        return None

    tokens = name.split()
    if not tokens:
        return None
    start = None
    for index, token in enumerate(tokens):
        if _NUMBER_RE.fullmatch(token):
            start = index
            break
    if start is None:
        return ''
    return ' '.join(tokens[start:])


def _letters_value(value: str, *, base: str) -> int:
    offset = ord(base) - 1
    result = 0
    for character in value:
        result = result * 26 + (ord(character) - offset)
    return result


def _default_name_getter(body: Any) -> Optional[str]:
    if isinstance(body, Mapping):
        value = body.get('name')
    else:
        value = getattr(body, 'name', None)
    return str(value) if value is not None else None
