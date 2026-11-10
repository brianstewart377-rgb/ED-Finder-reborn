# Ratings V4 Mechanics Evidence Audit

**Status:** accepted evidence authority frozen with scorer 4.0.0; see [freeze evidence](ratings-v4-freeze/README.md)
**Scope:** system-colonisation economy inheritance, local-body modifiers, strong/weak links, strong-link modifiers, top-two economy protection, and V4 scoring disposition

## Purpose

Ratings V4 must distinguish documented game mechanics from ED-Finder judgement. This audit records the mechanics evidence used to define the seven raw economy scores and identifies which facts belong instead to specialisation, archetype judgement, or Finder ranking.

The scoring architecture is:

```text
canonical facts
  -> mechanics features
  -> raw economy potential + specialisation quality
  -> archetype judgement
  -> Finder ranking
```

Raw economy scores must not silently absorb convenience heuristics such as distance from arrival, generic body diversity, generic strategic value, or complementary-economy strength.

## Evidence hierarchy

Use the strongest applicable evidence in this order:

1. Frontier first-party update/patch notes and official explanations.
2. Current Colonization Mega Guide where it consolidates or updates official rules.
3. Raven Colonial observed/modelled behaviour where first-party documentation is silent or ambiguous.
4. Current maintained community synthesis (for example HerzbubeWiki and comparable research).
5. Reproducible community experiments and issue reports.
6. Historical ED-Finder scorer behaviour only as regression evidence, never as mechanics authority.

Mechanics should carry an evidence/provenance grade so later discoveries can update a rule without redesigning the V4 schema.

## Sources reviewed

- Frontier Trailblazers Update 3, April 2025: https://forums.frontier.co.uk/threads/elite-dangerous-trailblazers-update-3-now-live.636973/post-10617548
- Elite Dangerous Vanguards Patch 1, 22 August 2025: https://store.steampowered.com/news/posts/?appids=359320 (patch text: top-two-economy protection)
- Colonization Mega Guide: https://thefloweringash.com/elite/colonisation/
- Herzbube EDColonisation synthesis: https://wiki.herzbube.ch/wiki/EDColonisation
- Elite Dangerous Wiki System Colonisation economy summary: https://elite-dangerous.fandom.com/wiki/System_Colonisation
- Recent community/research cross-checks, including Raven Colonial behaviour and 2026 advanced-guide discussions, used only where stronger sources are silent or known to be outdated.

## 1. Base inheritable economies

Frontier's Update 3 establishes that Colony-type ports inherit economies from their local body and that these body rules may stack with local modifiers.

| Local body | Base inheritable economies | V4 disposition |
|---|---|---|
| Black Hole | High Tech, Tourism | Direct raw-potential evidence for both; Tourism also gains a system-level strong-link modifier |
| Neutron Star | High Tech, Tourism | Direct raw-potential evidence for both; Tourism also gains a system-level strong-link modifier |
| White Dwarf | High Tech, Tourism | Direct raw-potential evidence for both; Tourism also gains a system-level strong-link modifier |
| Brown Dwarf | Military | Direct Military raw-potential evidence |
| Other/main-sequence stars | Military | Direct Military raw-potential evidence |
| Earth-like World | Agriculture, High Tech, Military, Tourism | Eligibility for all four, but not equal weighting: Agriculture/High Tech/Tourism have additional positive link evidence; Military does not |
| Water World | Agriculture, Tourism | Direct Agriculture/Tourism raw-potential evidence |
| Ammonia World | High Tech, Tourism | Direct High Tech/Tourism raw-potential evidence |
| Gas Giant | High Tech, Industrial | Direct High Tech/Industrial raw-potential evidence |
| High Metal Content | Extraction | Direct Extraction raw-potential evidence |
| Metal Rich | Extraction | Direct Extraction raw-potential evidence |
| Rocky Ice | Industrial, Refinery | Direct Industrial/Refinery raw-potential evidence |
| Rocky | Refinery | Direct Refinery raw-potential evidence |
| Icy | Industrial | Direct Industrial raw-potential evidence |

### V4 rule

**Inheritance establishes economic eligibility, not equal suitability.** V4 may weight an inherited economy more strongly when additional evidence shows that the same body/system also strengthens that economy or is a documented preferred location.

This prevents the old flat interpretation of an ELW as equally Agriculture/High Tech/Military/Tourism. ELW remains Military-capable, but its strongest V4 raw-economy evidence is Agriculture/Tourism and then High Tech because those have additional positive mechanics; Military receives inheritance evidence only.

## 2. Local-body inheritable modifiers

Frontier documents local-body attributes that add inheritable economies to Colony-type ports.

| Modifier | Added economy/economies | V4 disposition |
|---|---|---|
| Rings, including stellar asteroid belts | Extraction | Direct Extraction raw-potential evidence |
| Organics/Biologicals | Agriculture, Terraforming | Direct Agriculture raw-potential evidence; Terraforming is a separate mechanics feature, not one of the seven economy scores |
| Geological signals | Extraction, Industrial | Direct Extraction and Industrial raw-potential evidence |

### Important distinction: geologicals vs volcanism

These are different mechanics.

- **Geological signals** add inheritable Extraction + Industrial economy influence.
- **Volcanism** is a strong-link modifier for Extraction; it does not create Extraction inheritance by itself.

V4 must store these as separate feature types.

## 3. Strong-link mechanics

Frontier's Update 3 establishes:

- Strong links are local-body links between supporting facilities and ports.
- Weak links apply across non-local bodies.
- Weak links are not modified by local-body/system strong-link modifiers.
- Strong links are increased/decreased by environmental characteristics.

Current maintained references model strong-link base strength approximately by facility tier (commonly 0.4 / 0.8 / 1.2), but precise V4 economy suitability should not depend on hypothetical future facility placement unless computing an archetype/build scenario.

### V4 rule

Raw economy potential may use the **existence of favourable/hostile link conditions** as evidence of natural suitability. It must not pretend a supporting facility has already been built.

## 4. Strong-link modifier matrix

The source adapter applies reserve rules only where canonical attached body/ring
observations support them. References may describe these as system reserves;
the frozen scorer never infers a system-wide scalar from one observed body.
The synthetic system-reserve input remains a fixture convenience. See the data
contract for the frozen source-scope and unknown-state rules.

### Agriculture

**Positive:**
- Earth-like World
- Water World in current maintained research/reference
- terraformable body
- biologicals/organics

**Negative:**
- Icy body
- tidal-lock cases documented by Frontier/current research

**Evidence nuance:** Frontier's April 2025 notes explicitly list ELW, terraformable and organics as positive and icy/tidal-lock cases as negative. Current Mega Guide/Herzbube additionally lists Water World as a positive Agriculture modifier. The V4 ruleset should retain provenance/version for that Water World modifier.

**V4 disposition:** Agriculture raw potential is principally inherited Agriculture + Agriculture-specific modifiers. Terraformability amplifies an existing Agriculture opportunity; it does not by itself turn a non-Agriculture body into an Agriculture body.

### Extraction

**Positive:**
- Major or Pristine system reserves
- body volcanism

**Negative:**
- Low or Depleted reserves

**V4 disposition:** direct raw-potential amplifiers/penalties. Reserve level is a first-class V4 feature. Volcanism is an amplifier; geologicals are separate inheritance evidence.

### High Tech

**Positive:**
- Ammonia World
- Earth-like World
- Water World in current maintained references
- biologicals/organics
- geologicals

**Negative:** none currently established.

**Evidence nuance:** Frontier's April 2025 notes list Ammonia, ELW, geologicals and organics. Current Mega Guide/Herzbube also includes Water World. Preserve provenance for the added Water World rule.

**V4 disposition:** raw High-Tech potential should be grounded in inherited High Tech plus these mechanics. Generic scientific interest/body diversity belongs to a Research archetype, not raw High Tech.

### Industrial

**Positive:**
- Major or Pristine reserves

**Negative:**
- Low or Depleted reserves

**V4 disposition:** reserve level directly modifies raw Industrial potential. Extraction/Refinery strength must not inflate raw Industrial; their combination belongs to an industrial-chain archetype.

### Refinery

**Positive:**
- Major or Pristine reserves

**Negative:**
- Low or Depleted reserves

**V4 disposition:** reserve level directly modifies raw Refinery potential. Rocky/Rocky-Ice inheritance remains the principal native Refinery evidence.

### Tourism

**Positive:**
- Ammonia World
- Earth-like World
- Water World
- biologicals/organics
- geologicals
- Black Hole in system
- Neutron Star in system
- White Dwarf in system

**Negative:** none currently established.

**V4 disposition:** these are direct raw Tourism-potential evidence. Generic body diversity, rings, or merely having many bodies are not Tourism mechanics and should stay outside the raw score.

### Military

No established strong-link environmental modifiers were found in current authoritative/maintained references.

**V4 disposition:** raw Military potential should therefore be intentionally conservative. Main-sequence/Brown-Dwarf inherited Military is strong direct evidence; ELW Military inheritance is supporting eligibility. Generic strategic position, body count, slots, security potential, or Industrial strength belong to a Military Stronghold archetype/Finder profile, not the raw Military score.

## 5. Preferred local bodies / specialisation

The Mega Guide derives practical 'pure market' choices from inheritance and modifier rules:

| Economy | Preferred specialisation location |
|---|---|
| Extraction | HMC/Metal-Rich, preferably ringed, while avoiding modifiers that introduce unwanted competing economies when purity is the goal |
| Refinery | Rocky body, preferably clean of ring/bio/geo economy modifiers; ground slots matter because Refinery Hub is planetary |
| Industrial | Icy body, preferably clean of competing local modifiers |
| Military | Main-sequence/Brown-Dwarf star without asteroid belt when a pure inherited Military market is desired |
| Agriculture | Water World is a preferred native fit; organics can strengthen Agriculture; avoid negative Agriculture conditions |
| Tourism | Water/Ammonia or exotic non-main-sequence stellar environment depending build intent |
| High Tech | Ammonia or exotic non-main-sequence stellar environment depending build intent |

### V4 rule: potential and specialisation are different outputs

Every economy should expose at least:

- **potential 0-100:** how strongly the system can support the economy;
- **specialisation_quality 0-100:** how cleanly a build can make that economy dominant/top-two without unwanted inherited/modifier pressure.

A geo-rich HMC may have outstanding Extraction potential but lower Extraction purity because geologicals also introduce Industrial. V4 must not hide that distinction inside a mystery penalty.

## 6. Top-two economy protection

Frontier's Vanguards Patch 1 (22 August 2025) changed market behaviour so that goods produced by the **top two economies** are no longer consumed/cannibalised by linked ports or settlements of those economies.

This materially weakens the old assumption that any multi-economy system must be heavily penalised.

### V4 consequences

- Remove v3.4-style global cross-economy attenuation from raw economy scores.
- Do not lower Agriculture merely because Tourism/High Tech are also strong.
- Do not lower Extraction merely because Industrial is also present.
- Use `specialisation_quality` to describe how readily an economy can occupy a protected top-two position.
- Pair/archetype modelling may reward useful complementary combinations without altering the honest independent raw scores.

## 7. Stacking and modelling cautions

Maintained 2026 references add useful implementation detail that is not always explicit in Frontier's original prose:

- Same-economy local-body modifiers generally do not stack repeatedly with themselves/base economy in the straightforward additive way one might assume.
- Multiple modifiers that create the same inherited economy are commonly treated as one inherited contribution rather than unlimited stacking.
- Volcanism is a notable separate strong-link modifier and can stack with Extraction inheritance evidence.
- Strong-link positive/negative adjustments are commonly modelled as +/-0.4 with a floor, but these numeric values rely on guide/Raven observation where Frontier documentation is not complete.

### V4 disposition

Do not hard-wire speculative exact percentages into the core feature model. Store mechanic type, evidence version and derived contribution independently so coefficients can be recalibrated without schema change.

## 8. Rules from old ratings that are NOT raw economy mechanics

The following may remain useful elsewhere but are not supported as direct raw-economy mechanics:

| Factor | V4 home |
|---|---|
| Distance from arrival star | Finder accessibility / archetype practicality |
| Generic body diversity | Archetype/general system-interest dimension |
| Generic slot count | Buildability/archetype; only economy-specific where a documented facility requirement makes capacity material (for example Refinery ground hubs) |
| Generic compactness | Finder/archetype practicality |
| 'Strategic value' | Archetype/domain judgement |
| Nearby populated systems | Finder/logistics/colonisation planning |
| Complementary economy strength | Pair/archetype layer |
| Generic scientific interest | Research/High-Tech archetype, not raw High Tech |
| Current population | Existing-colony state/production analysis, not intrinsic pre-build economy potential |

## 9. Absolute scoring, not percentile scoring

V4 economy potential should be an **absolute mechanics-based 0-100 measure**, with the same semantic meaning across imports/generations.

Galaxy percentile/rank belongs to Finder and may be calculated separately.

## 10. Unknown is not zero

Missing evidence must not be scored as known absence.

Each economy result should carry:

- `potential_score`
- `specialisation_quality`
- `evidence_completeness`
- `confidence`
- contributor/explanation data
- mechanics/rule version

Examples:

```text
Tourism potential: 88
Evidence completeness: 64%
Confidence: Medium
```

is materially different from the same score at 99% completeness.

For geologicals, biologicals, rings, reserve level, terraformability and other source-sensitive facts, unknown coverage must remain unknown.

## 11. Seven-economy V4 interpretation

### Agriculture

Question: **How naturally and strongly can the known system support Agriculture through Agriculture-inheriting bodies/modifiers and Agriculture-specific link conditions?**

Core evidence:
- ELW
- Water World
- biologicals/organics
- positive Agriculture strong-link conditions

Terraformability is an amplifier, not standalone Agriculture inheritance.

### Refinery

Question: **How naturally can the system support Refinery markets and the local planetary infrastructure required to strengthen them?**

Core evidence:
- Rocky
- Rocky-Ice
- reserve level
- practical local ground capacity as a separate buildability/specialisation feature

### Industrial

Question: **How naturally and strongly can the system support Industrial economy influence?**

Core evidence:
- Icy
- Rocky-Ice
- Gas Giant
- geological modifier
- reserve level

### High Tech

Question: **How naturally and strongly can the known system support High-Tech economy influence under the documented inheritance/link mechanics?**

Core evidence:
- ELW
- Ammonia
- Gas Giant
- exotic stars (BH/Neutron/WD)
- documented High-Tech strong-link modifiers

Research/scientific-interest judgement is deliberately separate.

### Military

Question: **How naturally does the local-body inheritance model support Military economy influence?**

Core evidence:
- Main-sequence/Brown-Dwarf star inheritance
- ELW inheritance as secondary evidence

No generic strategic/archetype factors are included in raw Military potential.

### Tourism

Question: **How naturally and strongly can the system support Tourism through Tourism-inheriting worlds/exotic stellar bodies and Tourism-specific strong-link modifiers?**

Core evidence:
- ELW
- Water World
- Ammonia World
- BH/Neutron/White Dwarf
- biological/geological Tourism modifiers

### Extraction

Question: **How naturally and strongly can the system support Extraction through mineral bodies, rings/geological inheritance, reserve level and Extraction-specific link conditions?**

Core evidence:
- HMC
- Metal Rich
- rings/asteroid belts
- geologicals
- reserve level
- volcanism as amplifier

## 12. Evidence discrepancies that V4 must preserve explicitly

Some rules changed or were clarified after Frontier's April 2025 announcement. Do not erase that history.

Examples:

- Water World is listed by current maintained references as a High-Tech strong-link positive, while Frontier's April 2025 list did not include it.
- Water World Agriculture strong-link boosting is present in current maintained references but was not in the original Frontier April list.
- Community testing found periods where Frontier-documented terraformable Agriculture behaviour did not match live behaviour; later 2026 community references report changes/fixes. Treat this mechanic as versioned evidence rather than timeless fact.
- Tidal-lock behaviour has also received community corrections to the simple original wording.

V4 therefore needs a mechanics ruleset version and source provenance. A later game patch changes coefficients/rules and triggers a derived rebuild; it does not require a schema redesign.

## 13. Freeze criteria for the final Ratings V4 contract

Before declaring V4 scoring final:

1. Map every canonical V3 body/system field needed by the mechanics matrix.
2. Mark each input as authoritative, unknown-capable, or unavailable.
3. Define the exact normalization curve for each raw economy potential.
4. Define `specialisation_quality` independently from potential.
5. Define diminishing returns for multiple contributing bodies as an ED-Finder judgement rule, not a claimed Frontier mechanic.
6. Validate representative synthetic systems for each economy and mixed-economy edge cases.
7. Compare V4 against v3.4 only as regression/reference evidence, not as the target outcome.
8. Version the complete mechanics + coefficients manifest.
9. Only then freeze archetype inputs and derived database columns.

## Conclusion

The web mechanics review confirms the core V4 direction but rejects carrying the v3.4 formula forward as authority.

The durable V4 model is:

```text
mechanics-supported economy potential
+ separately stated specialisation quality
+ evidence completeness/confidence
-> archetype judgement
-> Finder ranking
```

This keeps game mechanics, ED-Finder judgement, build practicality and query preference separate enough to evolve independently.
