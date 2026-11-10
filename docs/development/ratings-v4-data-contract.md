# Ratings V4 Data Contract

**Status:** frozen V4.0 scorer/data representation authority; production bootstrap and publication remain pending
**Depends on:** `ratings-v4-mechanics-evidence.md`, `ratings-v4-scoring-contract.md`, `ratings-v4-validation-fixtures.md`

## Purpose

This contract maps Ratings V4 mechanics onto the retained V3 canonical data model and defines the rebuildable derived relations needed by the scorer. It deliberately avoids reshaping canonical generation tables merely to suit Ratings V4.

## 1. Source-data rule

Canonical V3 generation data remains source truth. Ratings V4 consumes explicit canonical facts through an adapter and writes only derived data.

Every mechanics input is classified as:

- **AVAILABLE** — directly present in the current canonical V3 generation or normalized supporting relation;
- **DERIVABLE** — reproducibly computed from available canonical facts;
- **MISSING_SOURCE** — required by the accepted mechanics model but not currently retained;
- **UNKNOWN_CAPABLE** — schema supports the fact, but absence must not be interpreted as observed false without completeness/provenance.

No `MISSING_SOURCE` value may be fabricated by the scorer.

## 2. Current V3 source mapping

The current published canonical generation is resolved through `v3_meta.current_canonical_generation` and `v3_meta.canonical_generation.relation_schema`; application/scorer code must never hard-code a particular `v3_gen_*` schema name.

| V4 input | Current V3 source | Status | V4 treatment |
|---|---|---|---|
| system id/name | generation `systems.id64/name` | AVAILABLE | canonical identity |
| x/y/z | generation `systems.x_ly/y_ly/z_ly` | AVAILABLE | Finder/spatial only; not raw economy scoring |
| region | `systems.galaxy_region_id` + `v3_vocab.galaxy_region` | AVAILABLE | Finder/map; not raw economy scoring |
| body identity | generation `bodies.body_pk`, `system_id64`, source/frontier body ids | AVAILABLE | scorer candidate identity; preserve source identity/provenance |
| broad body type | `bodies.body_type_id` + `v3_vocab.body_type` | AVAILABLE | distinguishes Star, Planet, Barycentre and Belt Cluster; does not establish planetary native inheritance |
| detailed body classification | Spansh `subType`, absent from the retained canonical body row | MISSING_SOURCE in canonical; separately retained enrichment for freeze validation | exact system/source-body identity join; retain the newer source snapshot and field hash separately |
| stellar class | `bodies.spectral_class`, `luminosity_class`, `is_main_star` | AVAILABLE | Military / exotic-star inheritance classification |
| landable | `bodies.is_landable` | AVAILABLE, UNKNOWN_CAPABLE | retain as a canonical fact; landability alone does not establish usable ground |
| usable ground opportunity | no retained canonical/source fact in the freeze cohort | MISSING_SOURCE | Refinery specialisation remains unresolved where this mandatory requirement matters |
| tidal lock | `bodies.is_tidally_locked` | AVAILABLE, UNKNOWN_CAPABLE | Agriculture modifier only under active mechanics rule |
| terraforming state | `bodies.terraforming_state_id` + vocab | AVAILABLE, UNKNOWN_CAPABLE | Agriculture strong-link amplifier; never standalone inheritance |
| volcanism | `bodies.volcanism_type_id` + vocab | AVAILABLE, UNKNOWN_CAPABLE | Extraction strong-link amplifier; distinct from geological signals |
| atmosphere classification | `bodies.atmosphere_classification_id` | AVAILABLE | retained for later archetypes; not current raw seven unless a ruleset explicitly uses it |
| rings / stellar asteroid belts | generation `rings`, retaining `kind=RING|BELT` | AVAILABLE, UNKNOWN_CAPABLE | both provide accepted Extraction modifier inheritance; kinds and provenance remain distinct; missing observations are unknown |
| biological signals | `body_signal_current.signal_count` + signal vocab; `bodies.signals_complete` | AVAILABLE/DERIVABLE, UNKNOWN_CAPABLE | explicit positive/zero counts are known; missing counts become absent only when body signal coverage is complete |
| geological signals | `body_signal_current.signal_count` + signal vocab; `bodies.signals_complete` | AVAILABLE/DERIVABLE, UNKNOWN_CAPABLE | same coverage rule; distinct from volcanism |
| system exotic-star presence | stellar bodies in generation | DERIVABLE | Tourism system modifier and High Tech/Tourism inheritance opportunities |
| main-sequence/Brown-Dwarf presence | stellar bodies in generation | DERIVABLE | Military inheritance opportunities |
| competing inherited economies at one body | body type + ring/bio/geo mechanics features | DERIVABLE | specialisation quality only |
| source freshness/provenance | generation source/lifecycle/completeness fields | AVAILABLE | evidence completeness/confidence |
| reserve level | generation `rings.reserve_type_id` + `v3_vocab.reserve_type`, with source/run/timestamp provenance | AVAILABLE, UNKNOWN_CAPABLE | attached body/ring observations only; no system-wide maximum or fallback |
| distance from arrival | body/station orbital-distance facts where present | AVAILABLE | archetype/Finder practicality only, not raw economy scores |
| generic diversity/compactness | canonical facts | DERIVABLE | archetype/Finder only |

## 3. Reserve-level source decision

The retained V3 generation already contains reserve observations. The recovered
canonical export and source-run metadata supersede the earlier assumption that
reserves were missing. `rings.reserve_type_id` joins `v3_vocab.reserve_type`;
the ring row retains:

```text
canonical body/ring reserve fact
- system_id64
- body_pk / ring_pk / source_ring_id64 where available
- reserve_type_id -> depleted / low / common / major / pristine
- source_id / source_run_id
- source_updated_at / freshness_checked_at
- lifecycle state
```

The adapter exposes a reserve only to the body to which the observations are
attached. Contradictory attached known reserve observations fail validation;
missing observations remain unknown. It sets `BodyFact.reserve_scope='body'`
even when the reserve is unknown, and never supplies a system reserve fallback.
The historical synthetic `SystemFacts.reserve_level` interface is not canonical
reserve authority. Do not add a guessed `systems.reserve_level` or broadcast a
system's best observed reserve to unrelated opportunities.

The lossless 12-system cohort contains 81 ring/belt rows and known reserves
attached to 46 bodies. Other body reserves lower evidence completeness; they
do not become Common, depleted or neutral observations.

### 3.1 Canonical adapter and source authority

[`ratings_v4_canonical.py`](../../apps/api/src/domain/ratings_v4_canonical.py)
implements `adapt_canonical_export(canonical_export, source_metadata,
subtype_sources) -> dict[int, SystemFacts]`. It validates source/run/artifact
identity, admitted canonical status, successful acquisition, exact body joins,
duplicate/conflicting identities and the exported body count. Barycentres and
belt-cluster placeholder bodies do not become local scoring candidates.

The actual V3 generation builder is the retained `v3_spansh.pipeline` /
`v3_spansh.adapter` package, version `v3-spansh-4c-correction-gate.1`; the legacy
`apps/importer/src/import_spansh.py` is not that builder. The adapter reads
exported generation facts and does not change either ingestion path or any
published canonical row. Production subtype ingestion requires a separately
reviewed recovery/integration of the V3 builder and canonical lifecycle.

[`tests/fixtures/ratings_v4_sources`](../../tests/fixtures/ratings_v4_sources/manifest.json)
retains the original canonical export, source-run metadata and all 12 original
Spansh API response bytes, with byte checksums and GitHub artifact provenance.
The canonical snapshot is effective 2026-08-24; the subtype API artifact was
retrieved 2026-09-08. Only `subType` is taken from the API artifact. Its distinct
source hash and body update timestamp accompany that field; canonical reserve,
ring, signal, stellar and landability facts retain canonical provenance.
`canonical_lineage` exposes both source identities for the derived manifest.

A successful complete acquisition and matching exported `loaded_body_count`
establish source catalogue inventory coverage. They do not establish complete
scan evidence for every body. `signals_complete` controls absent signal values;
explicit counts, including zero, remain observations regardless of that flag.
There is no equivalent retained per-body ring-completeness flag, so absent
ring/belt rows stay unknown even in a complete galaxy acquisition. Landability
does not establish the required Refinery ground opportunity.

## 4. Derived mechanics feature relation

V4 materializes normalized mechanics features so stored ratings can be rebuilt
and checked without reinterpreting raw canonical rows.

The following is the frozen logical representation, not production DDL:

```text
v3_derived.system_mechanics_feature
- derived_generation_id UUID
- system_id64 BIGINT
- subject_kind SYSTEM|BODY|RING
- body_pk BIGINT NULL
- subject_key TEXT
- feature_type TEXT
- feature_value_text TEXT NULL
- feature_value_numeric DOUBLE PRECISION NULL
- known_state KNOWN_PRESENT|KNOWN_ABSENT|UNKNOWN
- source_confidence DOUBLE PRECISION
- source_freshness_at TIMESTAMPTZ NULL
- rule_input_version TEXT
- provenance JSONB
PRIMARY KEY (derived_generation_id, system_id64, subject_kind, subject_key, feature_type)
```

Examples of `feature_type`:

- `BODY_CLASS_ELW`
- `BODY_CLASS_WATER_WORLD`
- `BODY_CLASS_AMMONIA`
- `BODY_CLASS_HMC`
- `BODY_CLASS_METAL_RICH`
- `BODY_CLASS_ROCKY`
- `BODY_CLASS_ROCKY_ICE`
- `BODY_CLASS_ICY`
- `BODY_CLASS_GAS_GIANT`
- `STAR_MAIN_SEQUENCE`
- `STAR_BROWN_DWARF`
- `STAR_NEUTRON`
- `STAR_WHITE_DWARF`
- `STAR_BLACK_HOLE`
- `RING_PRESENT`
- `BIOLOGICAL_PRESENT`
- `GEOLOGICAL_PRESENT`
- `VOLCANISM_PRESENT`
- `TERRAFORMABLE`
- `TIDALLY_LOCKED`
- `RESERVE_LEVEL`

This table is derived and rebuildable. It does not replace canonical normalized facts.

The implemented disposable proof in
[`scripts/ratings_v4/generation.py`](../../scripts/ratings_v4/generation.py)
uses only a new `ratings_v4_validation_*` schema. It stores normalized feature
identity plus a JSON payload containing known state, value, confidence and
provenance; economy-specific evidence weights remain in opportunity payloads.
That representation preserves the logical states above without claiming the
production `v3_derived` relations have been created.

## 5. Local economy opportunity relation

The scoring contract evaluates candidate local opportunities before system roll-up.

```text
v3_derived.economy_opportunity
- derived_generation_id UUID
- system_id64 BIGINT
- economy_id SMALLINT / economy_code TEXT
- candidate_kind SYSTEM|BODY
- body_pk BIGINT NULL
- mechanics_version TEXT
- scorer_version TEXT
- eligibility_class NATIVE|MODIFIER|NATIVE_AND_MODIFIER
- local_opportunity_score SMALLINT
- local_specialisation_quality SMALLINT NULL CHECK 0..100
- local_specialisation_quality_min SMALLINT CHECK 0..100
- local_specialisation_quality_max SMALLINT CHECK 0..100
- evidence_completeness DOUBLE PRECISION
- confidence DOUBLE PRECISION
- competing_economies TEXT[] / normalized child relation
- explanation JSONB
- computed_at TIMESTAMPTZ
PRIMARY KEY (derived_generation_id, system_id64, economy, candidate_kind, candidate identity)
```

This relation is useful for Inspect/explanations and for validating why the system-level score moved after a ruleset change.

## 6. System economy rating relation

Semantic authority is one row per system/economy, not one enormous historical-style ratings row.

```text
v3_derived.system_economy_rating
- derived_generation_id UUID
- system_id64 BIGINT
- economy_id SMALLINT / economy_code TEXT
- mechanics_version TEXT
- scorer_version TEXT
- potential_score SMALLINT CHECK 0..100
- specialisation_quality SMALLINT NULL CHECK 0..100
- specialisation_quality_min SMALLINT CHECK 0..100
- specialisation_quality_max SMALLINT CHECK 0..100
- evidence_completeness DOUBLE PRECISION CHECK 0..1
- confidence DOUBLE PRECISION CHECK 0..1
- best_candidate_kind SYSTEM|BODY
- best_candidate_body_pk BIGINT NULL
- best_specialisation_candidate_kind SYSTEM|BODY NULL
- best_specialisation_candidate_body_pk BIGINT NULL
- contributor_count INTEGER
- constraint_count INTEGER
- explanation JSONB
- computed_at TIMESTAMPTZ
PRIMARY KEY (derived_generation_id, system_id64, economy)
```

For hot Finder queries a separate wide projection may expose seven potential/specialisation columns, but it is only a performance projection. The normalized row-per-economy relation remains semantic authority.

Specialisation bounds represent unresolved mandatory constraints, conditional on
known inheritance/modifier evidence. The quality is null when the bounds differ;
an exact zero remains distinct from unknown. Store constraint rule IDs, states,
confidence, provenance and per-candidate intrinsic/constrained quality in the
explanation payload. The specialisation candidate is independent of the raw
potential candidate and is nullable for unresolved/zero specialisation. These
are frozen derived-data semantics, represented and round-tripped by the isolated
generation proof; they do not authorize a production migration. See
`ratings-v4-specialisation-constraints.md`.

## 7. Contributor relation

Explanation details should be queryable and testable without treating prose rationale as data authority.

```text
v3_derived.economy_rating_contributor
- derived_generation_id
- system_id64
- economy
- candidate_kind
- body_pk NULL
- rule_id
- mechanic_class NATIVE_INHERITANCE|MODIFIER_INHERITANCE|STRONG_LINK_POSITIVE|STRONG_LINK_NEGATIVE|SPECIALISATION_CONSTRAINT
- feature_type
- contribution_numeric
- evidence_grade
- provenance JSONB
PRIMARY KEY (... rule/candidate identity ...)
```

UI rationale is generated from these records.

## 8. Archetype boundary

Archetypes consume V4 ratings plus non-economy dimensions such as buildability, accessibility, body diversity, compactness, economy synergy and strategic/domain features.

Those dimensions must not be copied back into raw economy potential.

`v3_derived.system_archetype` remains separately versioned as defined by the spatial/derived-data decision.

## 9. Stable application boundary

The production application boundary remains stable published-generation
relations such as:

```text
v3_app.system_economy_rating
v3_app.system_economy_opportunity
v3_app.system_archetype
v3_app.system_search
```

The eventual production boundary resolves `v3_meta.current_derived_generation`
explicitly. No generation schema names or `search_path` assumptions leak into
application routes. V4 freeze validation does not create these application
views, publish a generation or enable a production route.

## 10. Freeze and production boundary

The implemented freeze path is the lossless source fixture -> validated
canonical adapter -> deterministic mechanics/scorer -> disposable derived
generation. The isolated writer stores source inputs, mechanics features,
opportunities, all seven economy ratings and contributors; it preserves
specialisation bounds and complete explanation payloads. Its generation
manifest binds input/content/metadata hashes and ruleset versions. Validation
checks persisted content and replay before marking the isolated generation
ready. No publication pointer or replacement of an existing schema is exposed.

Synthetic fixtures, real-system calibration and the separate V3.4 comparison
validate this scorer/data representation. Production remains subsequent work:
recover and review the V3 subtype ingestion boundary, establish governed
production derived-generation bootstrap/migration, build against the chosen
canonical generation, validate it and then publish through reviewed authority.
The freeze alone does not claim production readiness or authorize those changes.

## Acceptance rules

- No scorer input is silently defaulted from unknown to false/neutral.
- Missing attached reserve observations lower completeness; they do not fabricate reserve state or borrow another body's reserve.
- Geologicals and volcanism remain separate features.
- ELW inherited Military remains visible but receives no unsupported raw strategic bonus.
- Any coefficient-only V4.x change requires derived rebuild, not schema migration.
- Any mechanics-rule change updates version/provenance and triggers rebuild, not canonical mutation.
