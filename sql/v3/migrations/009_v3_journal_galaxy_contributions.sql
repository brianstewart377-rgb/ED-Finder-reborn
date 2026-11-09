-- Explicit journal offers reuse the baseline contribution/withdrawal ledger.
-- No existing private event receives consent or canonical eligibility here.
BEGIN;

CREATE TABLE v3_source.journal_physical_snapshot (
    artifact_id uuid PRIMARY KEY REFERENCES v3_source.source_artifact(artifact_id),
    sanitized_payload bytea NOT NULL,
    CHECK (octet_length(sanitized_payload) <= 2097152)
);
COMMENT ON TABLE v3_source.journal_physical_snapshot IS
    'Replayable reviewed physical observations only; no account, commander, filename or private payload fields. Exact bytes match source_artifact.content_sha256. Eligibility must still be checked via contribution receipts for every new publication.';

ALTER TABLE v3_private.private_fact
    ADD COLUMN journal_event_id uuid,
    ADD CONSTRAINT private_fact_journal_owner_fk
        FOREIGN KEY (journal_event_id, owner_account_id)
        REFERENCES v3_private.journal_event(journal_event_id, owner_account_id);

CREATE UNIQUE INDEX private_fact_journal_identity_idx
    ON v3_private.private_fact(owner_account_id, journal_event_id, fact_kind)
    WHERE journal_event_id IS NOT NULL;
COMMENT ON INDEX v3_private.private_fact_journal_identity_idx IS
    'Use: retry-safe normalized contribution identity without merging commander histories.';

CREATE INDEX journal_physical_observation_peers_idx
    ON v3_private.private_fact((fact_payload->>'system_id64'),
                              (fact_payload->>'frontier_body_id'), (fact_payload->>'observed_at'))
    WHERE fact_kind = 'journal_galaxy_physical_v1' AND withdrawn_at IS NULL;
COMMENT ON INDEX v3_private.journal_physical_observation_peers_idx IS
    'Use: find equal-time physical conflicts across bounded reconciliation batches.';

CREATE INDEX journal_contribution_review_idx
    ON v3_private.contribution_receipt(audience_code, sharing_policy_version, offered_at DESC, contribution_id DESC);
COMMENT ON INDEX v3_private.journal_contribution_review_idx IS
    'Use: bounded account/operator galaxy contribution receipt pages.';

CREATE INDEX canonical_evidence_contribution_idx
    ON v3_source.canonical_evidence_group(contribution_id, generation_id)
    WHERE contribution_id IS NOT NULL;
COMMENT ON INDEX v3_source.canonical_evidence_contribution_idx IS
    'Use: withdrawal lineage and contribution build/publication status.';

CREATE INDEX journal_physical_body_evidence_idx
    ON v3_source.canonical_evidence_group(generation_id, entity_key)
    WHERE entity_kind = 'body' AND field_group_code = 'JOURNAL_PHYSICAL';
COMMENT ON INDEX v3_source.journal_physical_body_evidence_idx IS
    'Use: retrieve per-body field freshness without rescanning all journal evidence in a generation.';

CREATE INDEX journal_eligible_keyset_idx
    ON v3_private.contribution_receipt(contribution_id)
    WHERE audience_code = 'ED_FINDER_GALAXY' AND contribution_state = 'ELIGIBLE';
COMMENT ON INDEX v3_private.journal_eligible_keyset_idx IS
    'Use: bounded keyset carry-forward of reviewed observations during future catalogue builds.';

CREATE VIEW v3_private.eligible_journal_galaxy_contribution AS
SELECT cr.contribution_id, cr.decided_at, pf.fact_payload
  FROM v3_private.contribution_receipt cr
  JOIN v3_private.private_fact pf USING (private_fact_id)
  JOIN v3_private.private_import pi ON pi.private_import_id = pf.private_import_id
  JOIN v3_private.journal_event e ON e.journal_event_id = pf.journal_event_id
  JOIN v3_identity.account_commander_access a
    ON a.account_id = e.owner_account_id AND a.commander_id = e.owner_commander_id
  JOIN v3_identity.account owner ON owner.account_id = a.account_id
  JOIN v3_identity.commander c ON c.commander_id = a.commander_id
 WHERE cr.audience_code = 'ED_FINDER_GALAXY'
   AND cr.sharing_policy_version = 'journal-galaxy-physical-v1'
   AND cr.contribution_state = 'ELIGIBLE'
   AND pf.fact_kind = 'journal_galaxy_physical_v1'
   AND cr.site_publication_allowed AND cr.api_redistribution_allowed
   AND pf.withdrawn_at IS NULL AND pi.import_state = 'READY'
   AND a.access_role = 'OWNER' AND a.revoked_at IS NULL
   AND owner.account_state = 'ACTIVE' AND c.commander_state = 'ACTIVE'
   AND EXISTS (SELECT 1 FROM v3_identity.commander_external_identity ce
                WHERE ce.commander_id = c.commander_id AND ce.provider = 'frontier'
                  AND ce.issuer = 'https://auth.frontierstore.net' AND ce.verified_at IS NOT NULL
                  AND ce.subject ~ '^F[1-9][0-9]{0,19}$');

CREATE FUNCTION v3_meta.guard_journal_contribution_publication()
RETURNS trigger LANGUAGE plpgsql AS $function$
DECLARE
    receipt record;
    applicable boolean;
BEGIN
    IF NEW.lifecycle_state NOT IN ('VALIDATING', 'READY', 'PUBLISHED') THEN
        RETURN NEW;
    END IF;
    -- Lock decisions until publication commits, so a concurrent withdrawal
    -- cannot be checked before it commits and then ignored during publication.
    FOR receipt IN
        SELECT cr.contribution_state, cr.site_publication_allowed,
               cr.api_redistribution_allowed, pf.withdrawn_at,
               pi.import_state, e.owner_account_id, e.owner_commander_id
          FROM v3_source.canonical_evidence_group eg
          JOIN v3_private.contribution_receipt cr USING (contribution_id)
          JOIN v3_private.private_fact pf USING (private_fact_id)
          JOIN v3_private.private_import pi ON pi.private_import_id = pf.private_import_id
          JOIN v3_private.journal_event e ON e.journal_event_id = pf.journal_event_id
         WHERE eg.generation_id = NEW.generation_id
           AND cr.audience_code = 'ED_FINDER_GALAXY'
         ORDER BY cr.contribution_id FOR SHARE OF cr, pf, pi
    LOOP
        IF receipt.contribution_state <> 'ELIGIBLE'
           OR NOT receipt.site_publication_allowed
           OR NOT receipt.api_redistribution_allowed
           OR receipt.withdrawn_at IS NOT NULL
           OR receipt.import_state <> 'READY' THEN
            RAISE EXCEPTION 'generation contains a withdrawn or ineligible journal contribution';
        END IF;
        PERFORM 1 FROM v3_identity.account_commander_access a
          JOIN v3_identity.account owner ON owner.account_id = a.account_id
          JOIN v3_identity.commander c ON c.commander_id = a.commander_id
         WHERE a.account_id = receipt.owner_account_id
           AND a.commander_id = receipt.owner_commander_id
           AND a.access_role = 'OWNER' AND a.revoked_at IS NULL
           AND owner.account_state = 'ACTIVE' AND c.commander_state = 'ACTIVE'
           AND EXISTS (SELECT 1 FROM v3_identity.commander_external_identity ce
                        WHERE ce.commander_id = c.commander_id AND ce.provider = 'frontier'
                          AND ce.issuer = 'https://auth.frontierstore.net' AND ce.verified_at IS NOT NULL
                          AND ce.subject ~ '^F[1-9][0-9]{0,19}$')
         FOR SHARE OF a, owner, c;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'generation contains an unowned journal contribution';
        END IF;
    END LOOP;
    -- Baseline finalization builds indexes before the enrichment phase. Only
    -- publication requires completeness, so bounded enrichment batches can run.
    -- A later Spansh refresh cannot silently erase previously reviewed facts.
    IF NEW.lifecycle_state = 'PUBLISHED' THEN
        EXECUTE format('SELECT EXISTS (
            SELECT 1 FROM v3_private.eligible_journal_galaxy_contribution candidate
            JOIN %I.bodies b
              ON b.system_id64 = (candidate.fact_payload->>''system_id64'')::bigint
             AND b.frontier_body_id = (candidate.fact_payload->>''frontier_body_id'')::bigint
             AND b.lifecycle_state = ''ACTIVE''
            WHERE candidate.decided_at <= $1
              AND NOT EXISTS (SELECT 1 FROM v3_source.canonical_evidence_group eg
                               WHERE eg.generation_id = $2
                                 AND eg.contribution_id = candidate.contribution_id))', NEW.relation_schema)
            INTO applicable USING NEW.created_at, NEW.generation_id;
        IF applicable THEN
            RAISE EXCEPTION 'generation omits an eligible journal contribution; reconcile before publication';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER journal_contribution_publication_guard
    BEFORE UPDATE OF lifecycle_state ON v3_meta.canonical_generation
    FOR EACH ROW EXECUTE FUNCTION v3_meta.guard_journal_contribution_publication();

COMMIT;
