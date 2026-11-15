// The two shapes the reader hands on, as the API takes them. They were
// borrowed from a second generated copy of the API's types; that copy is gone,
// so they are written out here, beside the only code that uses them.
/** One thing the journal recorded, ready to be sent. */
export interface JournalImportObservationInput {
  observation_key: string;
  source_file: string;
  source_offset?: number;
  event_type: 'ApproachBody' | 'CarrierJump' | 'CodexEntry' | 'Commander' | 'Died' | 'Disembark' | 'Docked' | 'Embark' | 'Fileheader' | 'FSDJump' | 'FSDTarget' | 'FSSAllBodiesFound' | 'FSSBodySignals' | 'FSSDiscoveryScan' | 'LeaveBody' | 'Liftoff' | 'LoadGame' | 'Location' | 'MultiSellExplorationData' | 'NavRoute' | 'NavRouteClear' | 'Resurrect' | 'SAAScanComplete' | 'SAASignalsFound' | 'Scan' | 'ScanOrganic' | 'Screenshot' | 'SellExplorationData' | 'SellOrganicData' | 'Touchdown';
  observed_at?: string | null;
  system_id64?: string | null;
  system_name?: string | null;
  subject_type: 'system' | 'body' | 'route';
  subject_id?: string | null;
  summary?: string | null;
  payload: Record<string, unknown>;
  privacy_boundary: Record<string, unknown>;
}

/** A journal event kept whole for Powerplay: what the API takes for one. */
export interface PowerplayJournalEventInput {
  observation_key: string;
  event_type: string;
  /** A date and time, as the journal wrote it. */
  observed_at: string;
  game_build?: string | null;
  source_payload: Record<string, unknown>;
}

export interface JournalParserState {
  system_id64: string | null;
  system_name: string | null;
  body_id: string | null;
  body_name: string | null;
  commander: string | null;
  game_version: string | null;
  game_build: string | null;
}

export interface JournalFileCheckpoint {
  version: 1;
  source_file: string;
  source_size: number;
  next_offset: number;
  line_number: number;
  last_record_hash: string | null;
  complete: boolean;
  state: JournalParserState;
}

export interface JournalFileInput {
  file: File;
  /** Byte offset at a previously returned line boundary. */
  offset?: number;
  /** Restores location/session state for events which omit those fields. */
  checkpoint?: JournalFileCheckpoint;
  /** Optional exclusive byte bound, useful for incremental parsing. */
  end_offset?: number;
}

export type JournalFileSource = File | JournalFileInput;

/**
 * Per-file provenance for the V3 journal lane. `content_sha256` is the
 * file-level dedupe identity (ADR-010 acquisition artifact identity): it is
 * computed over the whole raw file bytes and must never depend on the
 * filename or the byte range requested for parsing. Kept OUT of
 * `client_manifest.files` on purpose — the A-1 lane's `JournalImportFileRef`
 * is `extra='forbid'` server-side, so the existing A-1 wire contract must
 * not gain new fields.
 */
export interface JournalFileManifestEntry {
  name: string;
  content_sha256: string;
  size_bytes: number;
  event_count: number;
  line_count: number;
}

/** Live parse progress reported between files (worker → UI). */
export interface JournalParseFileProgress {
  files_processed: number;
  files_total: number;
  events_parsed: number;
}

export interface JournalImportParseSummary {
  files_processed: number;
  lines_read: number;
  observations_ready: number;
  skipped_lines: number;
  powerplay_events_ready: number;
  event_counts: Record<string, number>;
}

export interface JournalImportParseResult {
  client_manifest: {
    parser_version: string;
    files: Array<{ name: string; event_count: number }>;
  };
  /** V3 file-level provenance (SHA-256 + size) — see JournalFileManifestEntry. */
  file_manifest: JournalFileManifestEntry[];
  observations: JournalImportObservationInput[];
  powerplay_events: PowerplayJournalEventInput[];
  preview: JournalImportParseSummary;
  checkpoints: JournalFileCheckpoint[];
}
