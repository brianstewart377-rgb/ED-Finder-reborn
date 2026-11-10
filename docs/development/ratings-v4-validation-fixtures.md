# Ratings V4 Validation Fixtures

**Status:** accepted fixture contract, validated for frozen scorer 4.0.0; see [freeze evidence](ratings-v4-freeze/README.md) and the [source-aware cohort review](ratings-v4-freeze-comparison.md)
**Depends on:** `ratings-v4-mechanics-evidence.md`, `ratings-v4-scoring-contract.md`

## Purpose

These fixtures define expected **ordering and score bands** before implementation. Exact coefficients may move during tuning, but a candidate scorer that violates these expectations needs an explicit mechanics/product explanation rather than ad-hoc coefficient changes.

All fixtures assume complete/high-confidence evidence unless marked otherwise.

## F1 — Pure Rocky Refinery specialist

System contains multiple clean Rocky bodies, no rings, no biological/geological modifiers, favourable reserve state where relevant, and at least one usable planetary location.

Expected:

- Refinery potential: **excellent to exceptional**
- Refinery specialisation: **very high**
- Extraction/Industrial/Agriculture: low unless other facts create them
- A second/third clean Rocky raises depth but with diminishing returns

Acceptance invariant: clean Refinery purity must not be penalised merely because the system has many ordinary non-contributing bodies.

## F2 — Mixed Rocky Refinery contamination case

Same as F1 except the best Rocky bodies carry combinations of rings, biologicals and geologicals.

Expected:

- Refinery potential remains strong because native Refinery inheritance still exists
- Refinery specialisation is lower than F1 because competing inherited economies are introduced
- Extraction/Industrial/Agriculture rise according to the actual modifiers

Acceptance invariant: contamination changes specialisation more than raw Refinery potential.

## F3 — Pure Icy Industrial specialist

Several clean Icy bodies, favourable reserve state, no competing local modifiers.

Expected:

- Industrial potential: excellent/exceptional
- Industrial specialisation: very high
- Refinery/Extraction remain low absent supporting inheritance

## F4 — Rocky-Ice dual Industrial/Refinery system

Several Rocky-Ice bodies.

Expected:

- Industrial and Refinery both strong
- neither raw score is attenuated because the other is also strong
- specialisation for each is lower than a perfectly pure single-economy equivalent, but top-two protection prevents a severe penalty

## F5 — Extraction reserve ladder

Three otherwise identical HMC/Metal-Rich systems:

- F5a Pristine/Major reserves
- F5b Average/Common reserves
- F5c Low/Depleted reserves

Expected ordering:

`F5a Extraction > F5b Extraction > F5c Extraction`

The reserve difference must be visible in explanation components.

## F6 — Geological + volcanic Extraction system

HMC with geologicals and volcanism.

Expected:

- native Extraction from HMC
- additional Extraction + Industrial inheritance from geologicals
- volcanism strengthens Extraction but does not create a second inheritance event
- Extraction potential very high
- Extraction specialisation below a comparable clean HMC because Industrial pressure exists

## F7 — Water-World Agriculture/Tourism system

Multiple Water Worlds, no contradictory evidence.

Expected:

- Agriculture and Tourism both excellent/exceptional
- neither is attenuated because both are strong
- Military remains low
- High Tech only receives effects supported by the active mechanics ruleset/provenance

## F8 — ELW mixed-economy system

One or more ELWs.

Expected:

- Agriculture, Tourism and High Tech all receive meaningful positive support
- Military receives inheritance eligibility but no fabricated environmental/strategic bonus
- Agriculture/Tourism should normally outrank Military for an otherwise uncomplicated ELW fixture
- no global cross-economy attenuation

## F9 — Ammonia High-Tech/Tourism system

Ammonia World(s), otherwise uncomplicated.

Expected:

- High Tech and Tourism strong
- Agriculture/Military low absent independent support

## F10 — Exotic-star High-Tech/Tourism system

Black Hole, Neutron Star or White Dwarf with no unrelated strong economy bodies.

Expected:

- High Tech and Tourism are the principal raw economy opportunities
- Tourism benefits from any documented system-level exotic-star modifier
- Military does not inherit from the exotic star

## F11 — Main-sequence Military system

Main-sequence/Brown-Dwarf stellar environment with no ELW and no strong competing economy bodies.

Expected:

- Military is the principal raw economy score
- Military score is driven by inheritance, not invented strategic/slot bonuses
- specialisation is high where no asteroid-belt/modifier pressure introduces competition

## F12 — Deep-body diminishing returns

Compare:

- F12a: one perfect candidate for economy E
- F12b: two perfect candidates
- F12c: four perfect candidates
- F12d: twenty perfect candidates

Expected approximate bands from the proposed roll-up:

- one perfect: ~82
- two perfect: ~93
- three perfect: ~98
- four perfect: 100
- twenty perfect: still 100, never >100 and never rewarded beyond the best four

Acceptance invariant: quality dominates sheer catalogue size.

## F13 — One excellent vs many mediocre

Compare:

- one local opportunity score 100
- ten local opportunities around 45

Expected:

The single excellent-site system should rank at least comparably and normally above the many-mediocre system for raw economy potential.

This guards against body-count inflation.

## F14 — Unknown evidence vs known absence

Two otherwise identical systems:

- F14a rings/bio/geo/reserve facts are explicitly known absent/normal
- F14b those same evidence families are unknown

Expected:

- potential scores may be similar if no positive evidence is present
- F14a has materially higher evidence completeness
- F14b must not receive fake negative contributions for unknown inputs
- confidence guard must prevent F14b from presenting as near-certain

## F15 — Top-two protection

A system with two naturally strong compatible economies and a weaker third.

Expected:

- both top economies keep their independent raw potential
- the weaker economy is not artificially attenuated merely because it ranks third
- specialisation/archetype logic may describe competition, but raw scores remain honest

This explicitly rejects Ratings v3.4 global attenuation.

## F16 — Terraformability amplifier only

Two otherwise identical non-Agriculture-native bodies, one terraformable and one not.

Expected:

- terraformability alone must not manufacture Agriculture eligibility where no Agriculture inheritance exists

Then repeat with an Agriculture-eligible body:

- terraformability may improve Agriculture local opportunity according to the active mechanics ruleset

## F17 — Geologicals vs volcanism distinction

Compare:

- body with geologicals only
- body with volcanism only
- body with both

Expected:

- geologicals can add Extraction/Industrial inheritance
- volcanism only modifies an existing Extraction opportunity
- the two features must produce different explanation records and cannot be collapsed into one boolean

## F18 — Absolute-score stability

Run the same deterministic fixture against two canonical generations containing identical relevant facts but different unrelated catalogue population.

Expected:

- all V4 raw economy scores identical
- percentile/rank may differ outside Ratings V4

This proves scores are absolute rather than galaxy-relative.

## Real-system validation cohort

The following twelve systems are the fixed initial real-world V4 regression cohort. They were selected before the V4 implementation is run against them. The implementation may not tune coefficients merely to make these systems pass; a violated expectation needs a mechanics/product explanation and explicit review.

### R1 — Wregoe ZN-X c28-28

Role: user-known mining/refining system.

Expected V4 character:

- Extraction and Refinery should be among the strongest raw economies.
- Industrial may be strong where the canonical body mix independently supports it.
- Existing built station economies are comparison evidence only and must not feed the intrinsic rating.
- Failure caught: a scorer that cannot recognise an obvious mining/refining candidate from physical mechanics.

### R2 — Praea Euq WV-W b2-2

Role: user-known four-Water-World system; the four WWs form two orbital pairs.

Expected V4 character:

- Agriculture and Tourism should dominate the raw economy profile.
- Agriculture and Tourism should both show strong depth without either being attenuated because the other is strong.
- Military must not be elevated merely because WW/ELW-style mixed-economy rules exist elsewhere.
- The two-pair orbital topology belongs to archetype/planning judgement, not raw Agriculture/Tourism mechanics unless a later verified rule says otherwise.
- Failure caught: body-count inflation, cross-economy attenuation, or leaking topology into raw economy scoring.

### R3 — Sol

Role: broad mixed baseline.

Expected V4 character:

- Multiple credible economy families should appear from the varied stellar/body composition.
- No blanket "famous/mixed system" bonus is permitted.
- Common/ordinary reserve evidence should not behave like Pristine/Major.
- Failure caught: generic diversity or strategic-value heuristics leaking into raw economy scores.

### R4 — Achenar

Role: ELW-heavy mixed-economy stress case.

Expected V4 character:

- Agriculture, Tourism and High Tech should all receive strong ELW-backed support.
- Military receives legitimate inheritance evidence but should normally trail Agriculture/Tourism in an otherwise uncomplicated ELW-led interpretation because it lacks equivalent strong-link environmental support.
- Failure caught: flattening ELW into equal Agriculture/High Tech/Military/Tourism values.

### R5 — Alioth

Role: strong habitable-world evidence combined with poor/depleted resource conditions.

Expected V4 character:

- Agriculture/Tourism should remain healthy where the body facts support them.
- Extraction/Industrial/Refinery should reflect poor reserve evidence rather than dragging unrelated economies down.
- Failure caught: reserve modifiers being applied globally rather than economy-specifically.

### R6 — Maia

Role: exotic-star / Brown-Dwarf separation case.

Expected V4 character:

- Black-hole/exotic stellar evidence should support High Tech and Tourism.
- Brown-Dwarf inheritance should independently support Military.
- The scorer must explain both rather than turning exoticness into a universal bonus.
- Failure caught: generic "exotic system" scoring and incorrect Military inheritance.

### R7 — Borann

Role: deep icy/gas-giant, high-resource and diminishing-returns stress case.

Expected V4 character:

- Industrial/resource-related potential should be prominent where supported.
- Many contributing bodies must not create scores above 100 or overwhelm the quality-first roll-up.
- Failure caught: raw body-count domination.

### R8 — HD 38179

Role: resource-rich Extraction fixture with separate Brown-Dwarf Military evidence.

Expected V4 character:

- Extraction should be strong from the relevant mineral/resource facts.
- Military may also be credible for a different, explicitly inherited reason.
- Neither should silently boost the other.
- Failure caught: unrelated economy coupling.

### R9 — Col 285 Sector BW-U c3-5

Role: Industrial/Refinery dual-economy fixture.

Expected V4 character:

- Industrial and Refinery should both be strong where Icy/Rocky-Ice/gas-giant facts justify them.
- Neither raw score is reduced simply because the other is strong.
- Failure caught: survival of v3.4-style cross-economy attenuation.

### R10 — Smojoo ZE-R d4-109 (Musica Universalis)

Role: broad multi-role "kitchen sink" system.

Expected V4 character:

- Agriculture, Tourism, High Tech and resource-oriented economies may all be strong for different explainable reasons.
- The contributor output must show which bodies/rules caused each economy result.
- A single opaque universal raw score is not acceptable.
- Failure caught: explanation collapse and over-compression of genuinely multi-role systems.

### R11 — Thaile HW-V e2-7 (Three Worlds Nebula)

Role: exotic mixed-world fixture with ELW/WW/Ammonia/resource evidence.

Expected V4 character:

- Tourism and High Tech should be especially strong from exotic stellar/world evidence.
- Agriculture should independently benefit from ELW/WW evidence.
- Extraction should arise only from its own mineral/ring/geological/reserve evidence.
- Failure caught: one strong archetype contaminating unrelated raw economy scores.

### R12 — Deriv-Dar

Role: intrinsic-potential versus accessibility separation case.

Expected V4 character:

- Agriculture/Tourism can remain intrinsically strong from ELW/WW/terraformable mechanics even where attractive bodies are very distant from arrival.
- Raw economy potential must not be reduced merely because travel within the system is inconvenient.
- Finder/accessibility or archetype practicality may later penalise the system strongly.
- Failure caught: v3.4-style distance/compactness heuristics leaking back into raw economy suitability.

## Real-system acceptance rules

For the cohort above:

1. Record the exact canonical-generation facts consumed for every system.
2. Record the active mechanics ruleset and scorer version.
3. Evaluate expected **ordering and character**, not hand-picked exact scores, until coefficient calibration is frozen.
4. Existing station economies, popularity, fame and user knowledge are post-hoc comparison evidence only.
5. A system may expose an unexpected economy if the contributor trace proves real mechanics evidence; that is not automatically a failure.
6. Any unexplained major reversal versus the expected character must be reviewed before V4.0 is frozen.
7. The cohort is a regression set, not training data.

## Freeze rule

Do not freeze coefficients solely because all numerical tests pass. The fixture suite and real-system cohort must also demonstrate:

- explanations match the mechanics evidence;
- unknown remains unknown;
- specialisation and potential are visibly distinct;
- ELW is not flattened into equal Agriculture/HighTech/Military/Tourism treatment;
- no old v3.4 global attenuation survives;
- score changes from coefficient tuning are understandable from stored contributors;
- the twelve real systems retain believable, explainable economy ordering without tuning directly to their observed built economies.
