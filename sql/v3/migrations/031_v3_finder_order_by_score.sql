-- 031: Find's galaxy-wide lists sort by the score they show.
--
-- Find's galaxy-wide lists (Any, and one colony type) read a window of the
-- best 10,000 systems straight from an index. Until now that index was
-- ordered by the stored weighted key (score x confidence x completeness),
-- a number Find never shows, so its "best first" list was not best first:
-- on 21 November 2026 the best score in its first 100 came 43rd.
--
-- These indexes order the same rows by the score Find shows, then by the
-- weighted key (which now only breaks ties), then by id64. They are the
-- 011 indexes with the score put first: the same table, the same tier
-- floors and the same columns.
--
-- Additive: new indexes only. The 011 weighted-key indexes stay until the
-- economy rebuild replaces the colony type columns, so a build from before
-- this change still reads its own indexes.
--
-- Apply it once, after a fresh backup, at a quiet moment: no nightly job
-- running (an index build blocks writes to the table it reads, though not
-- reads). It is one transaction: if anything goes wrong it changes nothing.
-- A second run stops at the first index, which is already there.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2h';

CREATE INDEX system_archetype_summary_score
    ON v3_derived.system_archetype
       (derived_generation_id, best_colony_potential DESC,
        weighted_potential DESC NULLS LAST, system_id64);

-- The literal tier floors let PostgreSQL prove partial-index applicability for
-- generic plans, as in 011.

CREATE INDEX system_archetype_paradise_b_score
    ON v3_derived.system_archetype
       (derived_generation_id, paradise_score DESC, paradise_weighted_e4 DESC, system_id64)
    WHERE paradise_score >= 60;
CREATE INDEX system_archetype_paradise_a_score
    ON v3_derived.system_archetype
       (derived_generation_id, paradise_score DESC, paradise_weighted_e4 DESC, system_id64)
    WHERE paradise_score >= 76;
CREATE INDEX system_archetype_paradise_s_score
    ON v3_derived.system_archetype
       (derived_generation_id, paradise_score DESC, paradise_weighted_e4 DESC, system_id64)
    WHERE paradise_score >= 88;

CREATE INDEX system_archetype_mining_hub_b_score
    ON v3_derived.system_archetype
       (derived_generation_id, mining_hub_score DESC, mining_hub_weighted_e4 DESC, system_id64)
    WHERE mining_hub_score >= 60;
CREATE INDEX system_archetype_mining_hub_a_score
    ON v3_derived.system_archetype
       (derived_generation_id, mining_hub_score DESC, mining_hub_weighted_e4 DESC, system_id64)
    WHERE mining_hub_score >= 76;
CREATE INDEX system_archetype_mining_hub_s_score
    ON v3_derived.system_archetype
       (derived_generation_id, mining_hub_score DESC, mining_hub_weighted_e4 DESC, system_id64)
    WHERE mining_hub_score >= 88;

CREATE INDEX system_archetype_manufacturing_hub_b_score
    ON v3_derived.system_archetype
       (derived_generation_id, manufacturing_hub_score DESC, manufacturing_hub_weighted_e4 DESC, system_id64)
    WHERE manufacturing_hub_score >= 60;
CREATE INDEX system_archetype_manufacturing_hub_a_score
    ON v3_derived.system_archetype
       (derived_generation_id, manufacturing_hub_score DESC, manufacturing_hub_weighted_e4 DESC, system_id64)
    WHERE manufacturing_hub_score >= 76;
CREATE INDEX system_archetype_manufacturing_hub_s_score
    ON v3_derived.system_archetype
       (derived_generation_id, manufacturing_hub_score DESC, manufacturing_hub_weighted_e4 DESC, system_id64)
    WHERE manufacturing_hub_score >= 88;

CREATE INDEX system_archetype_megacomplex_b_score
    ON v3_derived.system_archetype
       (derived_generation_id, megacomplex_score DESC, megacomplex_weighted_e4 DESC, system_id64)
    WHERE megacomplex_score >= 60;
CREATE INDEX system_archetype_megacomplex_a_score
    ON v3_derived.system_archetype
       (derived_generation_id, megacomplex_score DESC, megacomplex_weighted_e4 DESC, system_id64)
    WHERE megacomplex_score >= 76;
CREATE INDEX system_archetype_megacomplex_s_score
    ON v3_derived.system_archetype
       (derived_generation_id, megacomplex_score DESC, megacomplex_weighted_e4 DESC, system_id64)
    WHERE megacomplex_score >= 88;

CREATE INDEX system_archetype_research_hub_b_score
    ON v3_derived.system_archetype
       (derived_generation_id, research_hub_score DESC, research_hub_weighted_e4 DESC, system_id64)
    WHERE research_hub_score >= 60;
CREATE INDEX system_archetype_research_hub_a_score
    ON v3_derived.system_archetype
       (derived_generation_id, research_hub_score DESC, research_hub_weighted_e4 DESC, system_id64)
    WHERE research_hub_score >= 76;
CREATE INDEX system_archetype_research_hub_s_score
    ON v3_derived.system_archetype
       (derived_generation_id, research_hub_score DESC, research_hub_weighted_e4 DESC, system_id64)
    WHERE research_hub_score >= 88;

CREATE INDEX system_archetype_stronghold_b_score
    ON v3_derived.system_archetype
       (derived_generation_id, stronghold_score DESC, stronghold_weighted_e4 DESC, system_id64)
    WHERE stronghold_score >= 60;
CREATE INDEX system_archetype_stronghold_a_score
    ON v3_derived.system_archetype
       (derived_generation_id, stronghold_score DESC, stronghold_weighted_e4 DESC, system_id64)
    WHERE stronghold_score >= 76;
CREATE INDEX system_archetype_stronghold_s_score
    ON v3_derived.system_archetype
       (derived_generation_id, stronghold_score DESC, stronghold_weighted_e4 DESC, system_id64)
    WHERE stronghold_score >= 88;

CREATE INDEX system_archetype_population_capital_b_score
    ON v3_derived.system_archetype
       (derived_generation_id, population_capital_score DESC, population_capital_weighted_e4 DESC, system_id64)
    WHERE population_capital_score >= 60;
CREATE INDEX system_archetype_population_capital_a_score
    ON v3_derived.system_archetype
       (derived_generation_id, population_capital_score DESC, population_capital_weighted_e4 DESC, system_id64)
    WHERE population_capital_score >= 76;
CREATE INDEX system_archetype_population_capital_s_score
    ON v3_derived.system_archetype
       (derived_generation_id, population_capital_score DESC, population_capital_weighted_e4 DESC, system_id64)
    WHERE population_capital_score >= 88;

CREATE INDEX system_archetype_flexible_b_score
    ON v3_derived.system_archetype
       (derived_generation_id, flexible_score DESC, flexible_weighted_e4 DESC, system_id64)
    WHERE flexible_score >= 60;
CREATE INDEX system_archetype_flexible_a_score
    ON v3_derived.system_archetype
       (derived_generation_id, flexible_score DESC, flexible_weighted_e4 DESC, system_id64)
    WHERE flexible_score >= 76;
CREATE INDEX system_archetype_flexible_s_score
    ON v3_derived.system_archetype
       (derived_generation_id, flexible_score DESC, flexible_weighted_e4 DESC, system_id64)
    WHERE flexible_score >= 88;
COMMIT;
