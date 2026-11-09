from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BeforeValidator

EconomyName = Literal[
    'Agriculture',
    'Refinery',
    'Industrial',
    'HighTech',
    'Military',
    'Tourism',
    'Extraction',
]
EconomyFilter = Union[Literal['any'], EconomyName]


def _normalise_economy_name(v: Any) -> Any:
    if v is None:
        return v
    if not isinstance(v, str):
        return v
    s = v.strip()
    if not s:
        return None
    if s in ('Agriculture', 'Refinery', 'Industrial', 'HighTech', 'Military', 'Tourism', 'Extraction', 'any'):
        return s
    if s.lower() in ('any', 'unknown'):
        return 'any'
    try:
        from edfinder_api.search_economies import economy_enum_value  # type: ignore
    except ImportError:
        return s
    enum_val = economy_enum_value(s)
    return enum_val if enum_val is not None else s


EconomyFilterField = Annotated[
    Optional[EconomyFilter],
    BeforeValidator(_normalise_economy_name),
]
EconomyNameField = Annotated[
    EconomyName,
    BeforeValidator(_normalise_economy_name),
]

