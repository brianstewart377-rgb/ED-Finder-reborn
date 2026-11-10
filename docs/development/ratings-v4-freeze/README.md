# Ratings V4.0 freeze

The frozen scorer is **4.0.0**, using **v4-mechanics-2026-09**. This freezes the
seven independent raw economy scores, constrained specialisation, evidence
semantics, canonical adapter and derived-data representation. Production
ingestion, application publication and Finder/archetype ranking are subsequent
implementation stages.

## Frozen decisions

Native inheritance is 75, modifier-only inheritance 55, and combined inheritance
85. Each distinct strong link adds or subtracts 10, with each sign capped at 25.
The best four local scores use weights 82/11/5/2 hundredths. Integer arithmetic
and nearest-even rounding eliminate platform-dependent half-point errors.
There is no raw overall score or cross-economy attenuation.

Specialisation remains independent of potential. Missing usable-ground evidence
leaves eligible Refinery specialisation unresolved, with explicit bounds; it
cannot become a raw-score penalty or an invented available building site.

## Evidence and verification

| Freeze gate | Retained evidence |
|---|---|
| Mechanics and coefficient contract | Accepted mechanics audit, scoring contract and hashed scorer implementation |
| Synthetic expectations | All 18 original fixtures plus evidence, constraints, identity, rounding and classification regressions |
| Canonical source mapping | Original checksummed export, run metadata and 12 exact API dumps; strict identity-joined subtype enrichment |
| Representative real systems | 12 systems, 360 physical bodies, all 84 economy outputs in `cohort.json` |
| Legacy comparison | All 84 formula comparisons; 44 major deltas and 73 pair-order reversals explained in the linked review |
| Derived representation | PostgreSQL 18 stores and reads back every input, feature, opportunity, rating and contributor; deterministic rebuild must match |
| Failure behavior | Partial inserts roll back; altered scores, inputs, features, contributors, versions or lineage cannot become READY |
| Reproducible identity | `manifest.json` pins source/rule hashes, coefficients, source lineage and expected cohort/input hashes |

The [comparison review](../ratings-v4-freeze-comparison.md) explains the corrected
source-aware results. For example, Wregoe retains Extraction 95, Industrial 85
and Refinery 75; its earlier system-wide reserve bonus was unsupported. The
four-Water-World system retains Agriculture 84 and Tourism 85. These corrections
did not require coefficient tuning.

V3.4 values are a replay of its unchanged seven-economy formulas on retained
canonical observations. They are explicitly not historical production exports.
The old compressed cohort remains a separate calibration regression because it
lost unknown states and broadcast reserves.

## Declared source limits

The canonical generation is `v3_gen_phase4c_full_20260827_r5`, from source run
`3a52e525-eb1e-5e96-90af-0da7ce1f4922`. Canonical observations retain their August
source history. Detailed body subtype alone uses the separately retained
September API snapshot. The source fixture manifest records both authorities.

Reserves are observed on 46 local bodies through canonical attached ring/belt
records. They are never broadcast to other bodies. Missing rings, incomplete
signals and unobserved reserves remain unknown. Complete catalogue acquisition
does not establish complete scanning. Rings and stellar asteroid belts both
retain the accepted Extraction mechanic. Barycentres are retained in source
evidence but excluded from physical local opportunities.

No usable-ground source exists in this cohort; ten systems therefore have
unresolved Refinery specialisation. Borann and HD 38179 have no known Refinery
inheritance and retain zero. Confidence reflects the rule's source-confidence
weights and completeness guard, not an empirically measured probability.

These declared unknowns are supported frozen behavior. New observations can
change a rebuilt generation without changing the coefficients. A mechanics or
coefficient change requires a new version and reviewed evidence.

## Reproduce

Use the repository's CPython 3.14 runtime and pinned test dependencies:

```text
python -m pytest tests/test_ratings_v4*.py -q
python scripts/ratings_v4/verify_freeze.py
```

For full persistence verification, set `RATINGS_V4_VALIDATION_DATABASE_URL` to a
disposable PostgreSQL 18 database and run:

```text
python scripts/ratings_v4/verify_freeze.py --postgres --receipt validation-receipt.json
```

The runner creates a fresh `ratings_v4_validation_*` schema and retains it for
inspection. It has no publication pointer and never writes canonical data.
`validation-receipt.json` records the successful local freeze rebuild. The
dedicated CI workflow repeats the contract and database tests on Linux and
retains its own independent receipt.

Source-code hashes normalize UTF-8 text to LF for Windows/Linux reproducibility.
Original source-artifact hashes are byte-exact and protected by Git attributes.
Do not regenerate the frozen manifest merely to make a failing check pass:
review changes and assign a new scoring/mechanics version where behavior changes.

The next production stage must recover the authoritative `v3_spansh` builder,
review canonical subtype enrichment and the derived bootstrap, then integrate a
published generation boundary. The isolated schema here proves representability
and rebuild behavior; it is not a production migration or scale benchmark.
