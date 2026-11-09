"""Resolve the current published V3 generation schema safely."""

from __future__ import annotations

import re

import asyncpg
from fastapi import HTTPException

CURRENT_SCHEMA = re.compile(r"^v3_gen_[a-z][a-z0-9_]{0,30}$")


async def current_generation_schema(pool: asyncpg.Pool) -> str | None:
    row = await pool.fetchrow(
        """
        SELECT generation.relation_schema
          FROM v3_meta.current_canonical_generation current
          JOIN v3_meta.canonical_generation generation USING(generation_id)
         WHERE current.singleton
        """
    )
    if row is None:
        return None
    schema = str(row["relation_schema"])
    if not CURRENT_SCHEMA.fullmatch(schema):
        raise HTTPException(500, "Unsafe canonical generation relation schema")
    return schema
