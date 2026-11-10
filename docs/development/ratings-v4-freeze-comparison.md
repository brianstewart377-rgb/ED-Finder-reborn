# Ratings V4 freeze comparison

Completed mechanics/source review of `4.0.0`, using the unchanged 75/55/85 bases, ±10 strong links, ±25 caps and 0.82/0.11/0.05/0.02 local roll-up. This is a code-and-evidence audit against the already documented expectations, not a claim of new expert-user acceptance or historical production output equality.

## Evidence and scope

The retained canonical export is the read-only 12-system export from GitHub Actions run 34271006659, artifact 10073799050. The source run is `3a52e525-eb1e-5e96-90af-0da7ce1f4922`; the admitted source SHA-256 is `b4944ab5d7537d7e3c370d55bf92a887bebf36e6db0ba1159c072c7cdf9e9868`, effective 2026-08-24T05:32:35Z. Exact Spansh API dumps came from run 34271850775, artifact 10074128265, retrieved 2026-09-08T19:56:40Z. The committed source-fixture manifest retains the original artifacts and byte checksums. API records enrich detailed `subType` only, using exact `(system_id64, source_body_id64)` joins; later API signals and reserves do not overwrite canonical observations.

There are 389 canonical records: 327 planets, 33 stars and 29 barycentres. All 389 have an exact API body-ID match. V4 excludes the 29 barycentres from local economy candidates. The original compressed cohort retained the same 360 stars/planets but collapsed missing feature states into false and broadcast a selected reserve level across each system. It is historical calibration evidence, not the source-faithful freeze baseline. The canonical source run explicitly declares complete source inventory; 185 individual bodies nevertheless lack complete signal observations, which remain unknown. Canonical loaded counts include barycentres while several source body counts exclude them, so those two counts must not be compared naively.

The V3.4 comparison executes the unchanged pure economy functions in `apps/importer/src/build_ratings.py` on retained canonical numerical signal counts and flags, plus the same exact subtype enrichment. It includes original pre-attenuation and post-attenuation values. These are **reconstructed formula reference values**, not exported historical production ratings. It does not replay the historical importer, whose signal parsing differs from canonical observations. In particular its nested source-dictionary geological parser and genus-based biological parser cannot be assumed to have produced canonical numerical counts. Nulls remain null in the retained legacy inputs, followed by the original legacy false/zero defaults; each such default is listed. No distances, signal counts or landability states are fabricated.

The seven legacy economy functions consume unweighted counts: the arrival-distance weights affect other legacy dimensions, not these seven scores. The V4 comparison therefore does not claim that arrival-distance removal explains an economy-score delta. Neither legacy overall scores nor Finder/archetype practicality are compared.

## Architectural reasons for the changes

All 84 score pairs are recorded below, with 44 changes of at least 25 points and 73 strict pair-order reversals flagged for inspection. The committed `tests/fixtures/ratings_v4_legacy_comparison.json` records every score and categorised review decision. `scripts/ratings_v4/compare_legacy.py` reproduces the complete old raw score, attenuation, numerical counts, retained inputs and defaults from the committed source fixtures. A reversal flag is a diagnostic, not a failed mechanic.

The trace review identifies these concrete, intended causes:

1. V3.4 reduces the third economy by 15% and all later economies by 30%. V4 removes that reduction from independent raw economy scores. The legacy pre/post fields show the exact amount for each economy; removal alone is never asserted to explain every change.
2. V3.4 sums capped body/signal counts across the entire system, frequently reaching 100. V4 scores inherited local opportunities, then keeps the best four with diminishing depth. Numeric signal magnitude is retained for the historical formula but is not turned into repeated V4 inheritance.
3. V3.4 applies tidal-lock and icy-body Agriculture penalties system-wide and mixes scoopability into Agriculture. V4 applies relevant environmental links locally and excludes scoopability. A system's many unrelated icy or tidally locked bodies cannot suppress its supported habitable-world opportunities.
4. V3.4 gives Refinery points for HMC bodies and mixes landability/CMM practicality into that raw score. V4 requires actual Refinery inheritance from Rocky/Rocky-Ice bodies; usable-ground evidence belongs to separately bounded specialisation. Many clean Rocky bodies no longer create an automatic 100 raw score.
5. V3.4 grants Military points for generic landable capacity and indirect exotic prestige. V4 uses stellar/ELW inheritance only. Multiple Brown Dwarf bodies can independently support Military even when the main star is scoopable.
6. V3.4 can grant HighTech/Tourism points merely for geological or biological signals, without native/inherited opportunity. V4 treats those as links on eligible local economies. V3.4 also tests the substring `ammonia` before testing `gas giant`: an ammonia-life gas giant is counted as an Ammonia world in the legacy reference. V4 retains the real gas-giant identity.
7. V4 retains resource modifiers only at the body where canonical attached-ring reserve evidence is observed; it never broadcasts them across the system. Stellar asteroid belts retain their accepted V4 Extraction inheritance. The legacy importer only ingested ring arrays, so the reconstructed legacy `has_rings` excludes BELT rows explicitly.

These causes are evidenced separately for each system below. The corrected source-faithful cohort changes 24 of the 84 old compressed candidate-3 raw values; 60 remain equal. Those changes are primarily reserve-scope corrections, plus exact nearest-even rounding at exact half-point boundaries. No coefficients were tuned to this cohort.

## Review against the 12 documented expectations

### R1 — Wregoe ZN-X c28-28

Extraction 95, Industrial 85 and Refinery 75 remain the leading three families, satisfying the mining/refining character without a system-wide Pristine bonus. The best Extraction body is A 1: Metal Rich native inheritance plus geological modifier gives base 85, and observed volcanism adds 10; its reserve is unknown, so no reserve bonus is invented. Four clean Rocky bodies and seven Rocky-Ice bodies explain viable Refinery but no longer sum to legacy 100. Industrial retains actual icy/gas-giant/Rocky-Ice/geo opportunities instead of legacy count saturation. HighTech 74 overtakes Military 70 because its real gas-giant opportunities are no longer attenuated to 40, while Military no longer receives generic landable bonuses. Legacy Tourism 20 falls to 0 because there is no native Tourism body; geological signals and the legacy ammonia-life gas-giant misclassification supplied that old score. Agriculture remains 0 from observed support, with incomplete evidence explicitly disclosed.

### R2 — Praea Euq WV-W b2-2

Four Water Worlds produce Agriculture 84 and Tourism 85. Industrial is also 85 and Extraction 84 from independently supported resource opportunities, so the revised source-faithful profile has the expected exceptional Agriculture/Tourism character without suppressing genuine resource strength. The old reference's 25 tidal-lock and 26 icy-body counts depressed Agriculture globally; V4's local links and removal of attenuation explain its rise from 21. Tourism rises from 39 through WW local quality and independent scoring. HighTech 75 is gas-giant native support, not WW inheritance; WW HighTech is a link only. A single Rocky-Ice opportunity supports Refinery 62 under the 0.82 leading weight; legacy HMC/landability mixing is removed. Military 62 reflects the principal star opportunity, not 24 landable bodies. The two orbital pairs have no role in raw scoring. This is compatible with R2's documented rule that an unexpected supported economy is not automatically a failure; there is no need to force strict exclusive dominance by the two habitable-world economies.

### R3 — Sol

Agriculture 87, Industrial 85, HighTech 84, Extraction 82 and Tourism 79 express the varied actual body mix. The legacy 17 tidal-lock and 16 icy counts depressed Agriculture to 23 before attenuation and 16 afterward; they no longer penalise Earth/Mars opportunities globally. Mars supplies the leading Agriculture opportunity and Earth leads HighTech/Tourism. Nine clean Rocky plus six Rocky-Ice bodies saturated old Refinery at 100; V4 yields 75 from local quality. Legacy Military's 28 landable-body count and body-count bonuses no longer outweigh Tourism and Extraction. Ordinary reserve evidence supplies no Pristine bonus. These changes explain every Agriculture reversal and the Refinery/Military reversals without a fame/diversity modifier.

### R4 — Achenar

Three ELWs support Agriculture 93, HighTech 85 and Tourism 83, all ahead of Military 75. New World leads the three habitable-world economies. Legacy HighTech and Military both saturated at 100 from ELW counts and landable/gas-giant additions; the independent local results correct that flattening. Two HMC, two Metal Rich and five geological signals independently support Extraction 94; no unexplained ELW-to-resource bonus is used. Two Rocky bodies provide Refinery 70 and gas-giant/geo opportunities provide Industrial 75; the inversion of those two economies reflects their inherited local quality instead of HMC points and surface constraints. R4 never required resource economies to rank below ELW economies.

### R5 — Alioth

Three ELWs give Agriculture 93, HighTech 85 and Tourism 83. Fourteen legacy tidal-lock penalties and attenuation suppressed Agriculture to 37; local scoring preserves the habitable-world strength. Fourteen clean Rocky bodies and 16 landable bodies drove legacy Refinery/Military saturation; V4 yields 75 for each. Depleted reserves affect only supported local resource observations, with Industrial 74 and Extraction 81. Unobserved reserve evidence on other bodies does not inherit a system-wide penalty. Geological/ring inheritance on Alioth 4 supports the leading Extraction opportunity, explaining its rise above the old 20 reference and its reversals with Military/Refinery/Industrial. The system is not being treated as globally pristine or globally depleted.

### R6 — Maia

Extraction 95 leads from Metal Rich geological/volcanic evidence at Maia A 2 a; Industrial 85 has separate icy/gas-giant/geo support. Brown Dwarfs independently provide Military 75 and the Black Hole supplies Tourism 70, keeping the two evidence families separate. Legacy 32-landable-body Military bonuses and count-saturated Refinery 100 no longer outrank Extraction. Agricultural biological inheritance yields 55 despite no ELW/WW: legacy Agriculture was pushed to 4 by the system's 32 tidal-lock and nine icy counts. Tourism rises from 38 through its real Black Hole inheritance and the system exotic link, not universal exoticness. Refinery 75 has no broadcast Pristine enhancement. HighTech 75 remains supported by the exotic/gas-giant identities.

### R7 — Borann

Industrial 85 remains strongest, with Extraction 83, HighTech 75 and Military 70. Twenty-three icy bodies create real Industrial depth but no unbounded sum. Extraction overtakes HighTech/Military because a geological/volcanic HMC at Borann A 1 and resource-bearing opportunities supply local inheritance; legacy Extraction 39 was then attenuated to 27 and did not generally recognise ring-bearing non-Rocky bodies. Tourism 0 removes the old geological-only score and the ammonia-life gas-giant misclassification; HighTech remains valid through actual gas giants. Refinery 0 correctly rejects legacy HMC points in a system without Rocky/Rocky-Ice Refinery inheritance. No economy exceeds 100.

### R8 — HD 38179

Extraction 100 is backed by four HMC/four Metal Rich bodies, including HD 38179 5 d with resource and geological/volcanic evidence. Military 75 has Brown Dwarf/steller inheritance independent of the source's 83 geological signals, and exceeds the legacy 25 which mainly reflected five landable bodies. Industrial 64 is modifier-only geological inheritance, rather than a capped global count of 20 attenuated to 14. Refinery 0 removes the old HMC-only 17; its reversal with Industrial follows inherited opportunity semantics. HighTech and Tourism 0 remove geological-only legacy points because the bodies lack their required inheritance. This is precisely the documented separation of Extraction and Military evidence.

### R9 — Col 285 Sector BW-U c3-5

Industrial 85 and Refinery 75 remain independently strong and compatible. Refinery's supported native-quality score is 75 without the old compressed system-wide Pristine bonus; it is not reduced because Industrial is strong. Extraction 93 independently leads from HMC/geological/volcanic bodies such as body 4. Agriculture 55 comes from actual biological inheritance; legacy Agriculture 9 was suppressed by 21 tidal locks and eight icy bodies elsewhere. HighTech 74 retains gas-giant inheritance and overtakes Military 62 when legacy attenuation and landable bonuses are removed. Tourism 0 removes signal-only points, explaining Agriculture/Tourism inversion. Three clean Rocky bodies, four Rocky-Ice bodies and eight HMC bodies explain the old Refinery saturation; only eligible local opportunities determine V4.

### R10 — Smojoo ZE-R d4-109

Tourism 85, Industrial 85, HighTech 84 and Agriculture 83 all have separate inheritance-backed contributors. The ELW/WW/Ammonia mix explains the three habitable-world scores; body 6 b is a leading contributor. Generic landable counts and global attenuation previously put Refinery/Military ahead of those world-backed economies; V4's 75 for both corrects that ordering. Extraction 74 comes from ring/geological opportunities, including body 6 e, even though this system has no HMC/Metal Rich bodies. Legacy Extraction's narrow geology/Rocky-ring formula reached 25 and then 17; V4's broader ring inheritance explains the large increase without contamination by an overall archetype score. All result components retain separate contributor traces.

### R11 — Thaile HW-V e2-7

Tourism 95 leads from real world inheritance and the Neutron-star system link; Agriculture 91 independently benefits from the ELW/WW evidence. Extraction 93 comes from the nine HMC/geological/volcanic opportunities, led by body B 1. HighTech 84 is strong without requiring it to outrank the resource/habitable economies: some HighTech links cannot create inheritance on a Water World. Legacy Refinery/Military 100 were driven by six clean Rocky/landable counts and global aggregation; V4 gives 75/70. The 10 legacy tidal-lock counts and attenuation depressed Agriculture to 22; their removal from unrelated local opportunities explains its reversals. Actual gas-giant/geo support gives Industrial 84 instead of old 25. All listed rank changes follow these separate supported families, not one exotic overall bonus.

### R12 — Deriv-Dar

Agriculture 93 and Tourism 85 remain intrinsically strong through ELW/WW/terraformability and Ammonia evidence, regardless of travel distance. HighTech 84 and Extraction 94 retain separate support. Eighteen global tidal-lock penalties plus attenuation depressed Agriculture to 53; V4 local conditions explain its rises above Refinery/HighTech/Military/Tourism. Gas giants supply Industrial 75 instead of 30; native-quality Refinery and stellar Military each score 75 without generic capacity additions. Tourism overtakes those two and HighTech through its own preferred world support. The archive's actual individual arrival distances remain retained; no score difference is incorrectly attributed to distance because neither compared seven-economy formula uses the weighted legacy distance counts.

## Unknown and specialisation review

Ten systems retain `specialisation_quality = null` for eligible Refinery opportunities, with lower/upper bounds and unknown usable-ground constraints. Borann and HD 38179 have no Refinery-inherited opportunities and retain known zero specialisation. This is not inferred from landability. Missing numerical signals in the legacy reference and missing feature observations in V4 are visible separately, including source provenance and the effect on V4 evidence completeness/confidence.

The current canonical generation exposes reserve evidence on attached rings/belts. The retained later API contains body `reserveLevel` fields, but those are not mixed into this canonical replay. The freeze contract must state this explicitly as an unknown-capable source limitation, together with unavailable usable-ground evidence. This review does not claim that those unavailable observations became complete merely because all scoring tests pass.

Every large numerical movement and pair-order reversal has an identified architectural/input cause above. The comparison finds no unexplained major reversal against the documented mechanics. R1/R9 retain strong supported native Refinery at 75 rather than the compressed fixture's artificial 85; R2's resource ties remain explicitly permitted independent opportunities. This completes the real-system/legacy-formula comparison criterion for the unchanged V4 coefficient set. The release manifest and integrated generation verification record the final frozen identity. Later expert feedback or additional source observations can inform a versioned V4.x change.

## Complete score comparison

Each cell below is `V3.4 post-attenuation → V4 (delta)`. The JSON report also retains all V3.4 pre-attenuation values and exact inputs.

| System | Agriculture | Refinery | Industrial | HighTech | Military | Tourism | Extraction |
|---|---|---|---|---|---|---|---|
| HD 38179 | 0 → 0 (+0) | 17 → 0 (-17) | 14 → 64 (+50) | 14 → 0 (-14) | 25 → 75 (+50) | 10 → 0 (-10) | 100 → 100 (+0) |
| Achenar | 55 → 93 (+38) | 32 → 70 (+38) | 29 → 75 (+46) | 100 → 85 (-15) | 100 → 75 (-25) | 42 → 83 (+41) | 44 → 94 (+50) |
| Maia | 4 → 55 (+51) | 100 → 75 (-25) | 100 → 85 (-15) | 52 → 75 (+23) | 75 → 75 (+0) | 38 → 70 (+32) | 53 → 95 (+42) |
| Sol | 16 → 87 (+71) | 100 → 75 (-25) | 100 → 85 (-15) | 85 → 84 (-1) | 70 → 74 (+4) | 35 → 79 (+44) | 35 → 82 (+47) |
| Thaile HW-V e2-7 | 22 → 91 (+69) | 100 → 75 (-25) | 25 → 84 (+59) | 58 → 84 (+26) | 100 → 70 (-30) | 46 → 95 (+49) | 77 → 93 (+16) |
| Alioth | 37 → 93 (+56) | 100 → 75 (-25) | 30 → 74 (+44) | 100 → 85 (-15) | 85 → 75 (-10) | 44 → 83 (+39) | 20 → 81 (+61) |
| Col 285 Sector BW-U c3-5 | 9 → 55 (+46) | 100 → 75 (-25) | 100 → 85 (-15) | 44 → 74 (+30) | 56 → 62 (+6) | 19 → 0 (-19) | 85 → 93 (+8) |
| Deriv-Dar | 53 → 93 (+40) | 84 → 75 (-9) | 30 → 75 (+45) | 100 → 84 (-16) | 68 → 75 (+7) | 60 → 85 (+25) | 100 → 94 (-6) |
| Smojoo ZE-R d4-109 | 23 → 83 (+60) | 100 → 75 (-25) | 100 → 85 (-15) | 68 → 84 (+16) | 85 → 75 (-10) | 49 → 85 (+36) | 17 → 74 (+57) |
| Borann | 0 → 0 (+0) | 3 → 0 (-3) | 100 → 85 (-15) | 78 → 75 (-3) | 57 → 70 (+13) | 20 → 0 (-20) | 27 → 83 (+56) |
| Praea Euq WV-W b2-2 | 21 → 84 (+63) | 18 → 62 (+44) | 100 → 85 (-15) | 36 → 75 (+39) | 57 → 62 (+5) | 39 → 85 (+46) | 71 → 84 (+13) |
| Wregoe ZN-X c28-28 | 0 → 0 (+0) | 100 → 75 (-25) | 100 → 85 (-15) | 40 → 74 (+34) | 54 → 70 (+16) | 20 → 0 (-20) | 85 → 95 (+10) |

## All strict pair-order reversals

These are the complete diagnostic pairs, including small reversals. Each system discussion above identifies the inherited families, legacy attenuation/capacity effects and any source classification issue responsible. Signs are the first economy minus the second economy.

### HD 38179

- Refinery / Industrial: +3 → -64.

### Achenar

- Agriculture / HighTech: -45 → +8.
- Agriculture / Military: -45 → +18.
- Agriculture / Extraction: +11 → -1.
- Refinery / Industrial: +3 → -5.
- HighTech / Extraction: +56 → -9.
- Military / Tourism: +58 → -8.
- Military / Extraction: +56 → -19.

### Maia

- Refinery / Extraction: +47 → -20.
- Industrial / Extraction: +47 → -10.
- Military / Extraction: +22 → -20.

### Sol

- Agriculture / Refinery: -84 → +12.
- Agriculture / Industrial: -84 → +2.
- Agriculture / HighTech: -69 → +3.
- Agriculture / Military: -54 → +13.
- Agriculture / Tourism: -19 → +8.
- Agriculture / Extraction: -19 → +5.
- Refinery / HighTech: +15 → -9.
- Refinery / Tourism: +65 → -4.
- Refinery / Extraction: +65 → -7.
- Military / Tourism: +35 → -5.
- Military / Extraction: +35 → -8.

### Thaile HW-V e2-7

- Agriculture / Refinery: -78 → +16.
- Agriculture / Industrial: -3 → +7.
- Agriculture / HighTech: -36 → +7.
- Agriculture / Military: -78 → +21.
- Refinery / Industrial: +75 → -9.
- Refinery / HighTech: +42 → -9.
- Refinery / Tourism: +54 → -20.
- Refinery / Extraction: +23 → -18.
- Industrial / Military: -75 → +14.
- HighTech / Military: -42 → +14.
- HighTech / Tourism: +12 → -11.
- Military / Tourism: +54 → -25.
- Military / Extraction: +23 → -23.
- Tourism / Extraction: -31 → +2.

### Alioth

- Agriculture / Refinery: -63 → +18.
- Agriculture / HighTech: -63 → +8.
- Agriculture / Military: -48 → +18.
- Agriculture / Tourism: -7 → +10.
- Refinery / Tourism: +56 → -8.
- Refinery / Extraction: +80 → -6.
- Industrial / Extraction: +10 → -7.
- Military / Tourism: +41 → -8.
- Military / Extraction: +65 → -6.

### Col 285 Sector BW-U c3-5

- Agriculture / Tourism: -10 → +55.
- Refinery / Extraction: +15 → -18.
- Industrial / Extraction: +15 → -8.
- HighTech / Military: -12 → +12.

### Deriv-Dar

- Agriculture / Refinery: -31 → +18.
- Agriculture / HighTech: -47 → +9.
- Agriculture / Military: -15 → +18.
- Agriculture / Tourism: -7 → +8.
- Refinery / Tourism: +24 → -10.
- HighTech / Tourism: +40 → -1.
- Military / Tourism: +8 → -10.

### Smojoo ZE-R d4-109

- Agriculture / Refinery: -77 → +8.
- Agriculture / Military: -62 → +8.
- Refinery / HighTech: +32 → -9.
- Refinery / Tourism: +51 → -10.
- HighTech / Military: -17 → +9.
- HighTech / Tourism: +19 → -1.
- Military / Tourism: +36 → -10.

### Borann

- HighTech / Extraction: +51 → -8.
- Military / Extraction: +30 → -13.

### Praea Euq WV-W b2-2

- Agriculture / HighTech: -15 → +9.
- Agriculture / Military: -36 → +22.
- HighTech / Military: -21 → +13.
- Military / Tourism: +18 → -23.
- Tourism / Extraction: -32 → +1.

### Wregoe ZN-X c28-28

- Refinery / Extraction: +15 → -20.
- Industrial / Extraction: +15 → -10.
- HighTech / Military: -14 → +4.
