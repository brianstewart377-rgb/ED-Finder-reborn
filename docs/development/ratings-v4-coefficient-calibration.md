# Ratings V4 Coefficient Calibration

**Status:** calibration history; the revised coefficients are frozen in scorer 4.0.0. See [freeze evidence](ratings-v4-freeze/README.md).
**Depends on:** `ratings-v4-scoring-contract.md`, `ratings-v4-validation-fixtures.md`

## Finding

The initial scoring-contract candidate (`native=60`, `modifier-only=45`, `native+modifier=68` with system roll-up `82/11/5/2`) systematically undershoots the fixture expectations for straightforward strong systems.

Example: a Water World with native Agriculture/Tourism plus one documented positive strong-link condition scores about 70 locally under the initial coefficients. Two such candidates roll up to about 65, which is only `Strong`, while fixture F7 expects multiple Water Worlds to be `Excellent` to `Exceptional`.

The fixture suite therefore correctly rejects the initial coefficient set before implementation.

## Revised calibration candidate

Retain the architecture and depth curve but raise the meaning of mechanics-backed local inheritance:

| Evidence | Initial | Revised candidate |
|---|---:|---:|
| Native inheritance | 60 | 75 |
| Modifier inheritance only | 45 | 55 |
| Native + modifier inheritance | 68 | 85 |
| Distinct positive strong-link mechanic | +10 | +10 |
| Distinct negative strong-link mechanic | -10 | -10 |

The frozen strong-link caps are +/-25, applied separately to each sign.

The frozen system roll-up is:

```text
0.82*s1 + 0.11*s2 + 0.05*s3 + 0.02*s4
```

because the identified failure is primarily local opportunity calibration, not body-depth weighting.

## Sanity outcomes

Approximate examples with complete evidence:

- one native-only candidate at 75 -> system potential ~62 (`Strong`);
- two native-only candidates -> ~70 (`Excellent` threshold);
- one native + one positive strong-link condition at 85 -> ~70 (`Excellent`);
- two such 85 candidates -> ~79 (`Excellent`);
- four such candidates -> 85 (`Exceptional`);
- native+modifier at 85 plus one strong-link positive -> 95 local; one site -> ~78 system, two -> ~88 system;
- a fully supported 100 local candidate -> 82 system, preserving room for demonstrated system depth.

This gives a useful interpretation: a single genuinely good native opportunity makes a system `Strong`; repeated/favourably-modified opportunities make it `Excellent`; exceptional system scores require either very high local support or meaningful depth.

## Military consequence

Military has no established environmental strong-link modifiers. Under the frozen coefficients, one ordinary-star/Brown-Dwarf native Military opportunity is 62 at system level and several independent Military-native opportunities can reach the low/mid 70s. The reviewed cohort supports this conservative raw Military treatment; a Military Stronghold archetype may incorporate capacity, Industrial support, geography and other strategic dimensions.

## Freeze rule

The freeze evaluated all 18 deterministic fixtures, the evidence/constraint regressions, all 84 source-aware cohort scores and the reviewed V3.4 formula comparisons. PostgreSQL 18 verified persistence and deterministic rebuild. The revised coefficients needed no further tuning; exact nearest-even rounding is now explicit. Future coefficient changes require a new scorer version and derived rebuild, with reviewed regression evidence.
