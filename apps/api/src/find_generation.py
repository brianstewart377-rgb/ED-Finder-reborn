"""Find: the published data set every Finder read is pinned to.

A search is read from the published derived generation, and only when both
Finder products (`system_search`, `system_archetype`) are READY and
published for it; otherwise it answers 404 or 503 and says which is missing.
Moved here from local_search.py on 15 November 2026.
"""
from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import HTTPException


# The derived products a V3 Finder ranked read depends on. A generation may be
# published before either Finder product is attached, and a READY product stays
# hidden until its separate publication audit commits. Requiring both products
# to be READY and published keeps every unavailable state distinct from a valid
# search with no matches.
_REQUIRED_FINDER_PRODUCTS: frozenset[str] = frozenset({'system_search', 'system_archetype'})


async def _current_derived_generation(connection: asyncpg.Connection) -> dict[str, Any]:
    row = await connection.fetchrow('''
        SELECT c.derived_generation_id, c.publication_sequence
        FROM v3_meta.current_derived_generation c
        JOIN v3_meta.derived_generation d USING(derived_generation_id)
        WHERE d.lifecycle_state='PUBLISHED'
    ''')
    if row is None:
        raise HTTPException(404, 'No published Ratings V4 generation')

    # The v3_app views intentionally expose only published products. Inspect
    # the product rows themselves so a 503 can name whether each dependency is
    # missing, unfinished/failed, or READY but not yet published.
    products = await connection.fetch(
        '''
        SELECT product_code,
               lifecycle_state,
               published_at,
               product_version AS archetype_version,
               encode(manifest_sha256, 'hex') AS archetype_manifest_sha256,
               manifest ->> 'archetype_model_sha256' AS archetype_model_sha256,
               manifest ->> 'model_file_sha256' AS model_file_sha256
        FROM v3_meta.derived_product
        WHERE derived_generation_id = $1
          AND product_code = ANY($2::text[])
        ''',
        row['derived_generation_id'],
        list(_REQUIRED_FINDER_PRODUCTS),
    )
    by_code = {product['product_code']: product for product in products}
    unavailable: list[str] = []
    for product_code in sorted(_REQUIRED_FINDER_PRODUCTS):
        product = by_code.get(product_code)
        if product is None:
            unavailable.append(f'{product_code}=MISSING')
        elif product['lifecycle_state'] != 'READY':
            unavailable.append(f'{product_code}={product["lifecycle_state"]}')
        elif product['published_at'] is None:
            unavailable.append(f'{product_code}=READY-unpublished')
    if unavailable:
        raise HTTPException(
            503,
            'Finder ranking products are not available for the published '
            f'generation ({", ".join(unavailable)}).',
        )

    archetype_product = next(
        product for product in products if product['product_code'] == 'system_archetype'
    )
    identity = {
        'archetype_version': archetype_product['archetype_version'],
        'archetype_manifest_sha256': archetype_product['archetype_manifest_sha256'],
        'archetype_model_sha256': archetype_product['archetype_model_sha256'],
    }
    if any(not value for value in identity.values()):
        raise HTTPException(
            503,
            'Finder archetype product identity is incomplete for the published generation.',
        )
    return {
        **dict(row),
        **identity,
        'model_file_sha256': archetype_product.get('model_file_sha256'),
    }


async def resolve_published_generation(pool: asyncpg.Pool) -> dict[str, Any]:
    """Resolve the current PUBLISHED derived generation (id + sequence).

    Public wrapper around `_current_derived_generation` for callers (the
    search router's cache-key builder) that need the pinned generation
    *before* entering the ranked read — so a warm cache entry can never be
    served while no generation is published (404), and the cache key can be
    scoped to the exact generation the results would be computed under.
    """
    async with pool.acquire() as conn:
        return await _current_derived_generation(conn)
