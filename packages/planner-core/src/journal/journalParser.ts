import type {
  JournalImportObservationInput,
  PowerplayJournalEventInput,
} from '@ed-finder/api-client/types';
import { decimalString, parseJournalJson, toJournalTransportValue } from './journalJson';
import type {
  JournalFileCheckpoint,
  JournalFileInput,
  JournalFileManifestEntry,
  JournalFileSource,
  JournalImportParseResult,
  JournalParseFileProgress,
  JournalParserState,
} from './types';

export const JOURNAL_PARSER_VERSION = 'journal-import-worker-v3';

export const SUPPORTED_JOURNAL_EVENTS = [
  'ApproachBody',
  'CarrierJump',
  'CodexEntry',
  'Commander',
  'Died',
  'Disembark',
  'Docked',
  'Embark',
  'Fileheader',
  'FSDJump',
  'FSDTarget',
  'FSSAllBodiesFound',
  'FSSBodySignals',
  'FSSDiscoveryScan',
  'LeaveBody',
  'Liftoff',
  'LoadGame',
  'Location',
  'MultiSellExplorationData',
  'NavRoute',
  'NavRouteClear',
  'Resurrect',
  'SAAScanComplete',
  'SAASignalsFound',
  'Scan',
  'ScanOrganic',
  'Screenshot',
  'SellExplorationData',
  'SellOrganicData',
  'Touchdown',
] as const;

type JournalEventType = typeof SUPPORTED_JOURNAL_EVENTS[number];

const SUPPORTED_EVENT_SET = new Set<string>(SUPPORTED_JOURNAL_EVENTS);
const POWERPLAY_EVENT_SET = new Set([
  'Location', 'FSDJump', 'Powerplay', 'PowerplayCollect', 'PowerplayDeliver',
  'PowerplayMerits', 'PowerplayRank', 'PowerplayJoin', 'PowerplayLeave',
  'PowerplayDefect',
]);
const POWERPLAY_SYSTEM_FIELDS = [
  'ControllingPower', 'Powers', 'PowerplayState',
  'PowerplayStateControlProgress', 'PowerplayStateReinforcement',
  'PowerplayStateUndermining',
  'PowerplayConflictProgress',
] as const;
const POWERPLAY_PERSONAL_FIELDS = [
  'Power', 'FromPower', 'ToPower', 'Rank', 'Merits', 'MeritsGained',
  'TotalMerits', 'Count', 'Type', 'TimePledged', 'Votes',
] as const;
const BODY_EVENTS = new Set<JournalEventType>([
  'ApproachBody',
  'CodexEntry',
  'Disembark',
  'Embark',
  'FSSBodySignals',
  'LeaveBody',
  'Liftoff',
  'SAAScanComplete',
  'SAASignalsFound',
  'Scan',
  'ScanOrganic',
  'Screenshot',
  'Touchdown',
]);

// V3 observation events that carry the rolling Fileheader/LoadGame
// GameVersion/GameBuild state into their payloads (V3 journal lane). The
// existing lowercase `gameversion`/`gamebuild` attach for the A-1 lane is
// untouched; this is the capitalized V3 contract the server-side payload
// allowlists additionally admit (see the V3 event contract).
const GAME_VERSION_ATTACH_EVENTS = new Set<JournalEventType>([
  'Scan',
  'FSSBodySignals',
  'SAASignalsFound',
  'SAAScanComplete',
  'CodexEntry',
  'ScanOrganic',
  'SellOrganicData',
  'FSSDiscoveryScan',
  'FSSAllBodiesFound',
]);

export const EVENT_PAYLOAD_FIELDS: Record<JournalEventType, readonly string[]> = {
  ApproachBody: ['StarSystem', 'SystemAddress', 'Body', 'BodyID', 'BodyName'],
  CarrierJump: ['StarSystem', 'SystemAddress', 'StarPos', 'Body', 'BodyID', 'BodyType', 'Docked'],
  CodexEntry: ['EntryID', 'Name', 'Name_Localised', 'SubCategory', 'SubCategory_Localised', 'Category', 'Category_Localised', 'Region', 'System', 'SystemName', 'StarSystem', 'SystemAddress', 'BodyID', 'NearestDestination', 'NearestDestination_Localised', 'Latitude', 'Longitude', 'Traits'],
  Commander: ['Name', 'FID'],
  Died: ['KillerName', 'KillerShip', 'KillerRank', 'Killers'],
  Disembark: ['SRV', 'Taxi', 'Multicrew', 'StarSystem', 'SystemAddress', 'Body', 'BodyID', 'BodyName', 'OnStation', 'OnPlanet'],
  Docked: ['StarSystem', 'SystemAddress', 'StationName', 'StationType', 'MarketID', 'DistFromStarLS', 'StationGovernment', 'StationAllegiance', 'StationServices', 'StationEconomies', 'Taxi', 'Multicrew'],
  Embark: ['SRV', 'Taxi', 'Multicrew', 'StarSystem', 'SystemAddress', 'Body', 'BodyID', 'BodyName', 'OnStation', 'OnPlanet'],
  Fileheader: ['part', 'language', 'Odyssey', 'gameversion', 'build'],
  FSDJump: ['StarSystem', 'SystemAddress', 'StarPos', 'StarClass', 'JumpDist', 'FuelUsed', 'FuelLevel'],
  FSDTarget: ['Name', 'SystemAddress', 'StarClass', 'RemainingJumpsInRoute'],
  FSSAllBodiesFound: ['StarSystem', 'SystemAddress', 'Count'],
  FSSBodySignals: ['StarSystem', 'SystemAddress', 'BodyName', 'BodyID', 'Signals'],
  FSSDiscoveryScan: ['StarSystem', 'SystemAddress', 'Progress', 'BodyCount', 'NonBodyCount'],
  LeaveBody: ['StarSystem', 'SystemAddress', 'Body', 'BodyID', 'BodyName'],
  Liftoff: ['StarSystem', 'SystemAddress', 'Body', 'BodyID', 'BodyName', 'Latitude', 'Longitude', 'PlayerControlled', 'NearestDestination', 'NearestDestination_Localised'],
  LoadGame: ['Commander', 'FID', 'Horizons', 'Odyssey', 'Ship', 'Ship_Localised', 'ShipID', 'ShipName', 'ShipIdent', 'FuelLevel', 'FuelCapacity', 'GameMode', 'Group', 'Credits', 'Loan'],
  Location: ['StarSystem', 'SystemAddress', 'StarPos', 'Body', 'BodyID', 'BodyType', 'Docked', 'StationName', 'StationType', 'MarketID', 'Latitude', 'Longitude'],
  MultiSellExplorationData: ['Discovered', 'BaseValue', 'Bonus', 'TotalEarnings'],
  NavRoute: ['Route'],
  NavRouteClear: [],
  Resurrect: ['Option', 'Cost', 'Bankrupt'],
  SAAScanComplete: ['StarSystem', 'SystemName', 'SystemAddress', 'BodyName', 'BodyID', 'ProbesUsed', 'EfficiencyTarget'],
  SAASignalsFound: ['StarSystem', 'SystemAddress', 'BodyName', 'BodyID', 'Signals', 'Genuses'],
  Scan: ['ScanType', 'StarSystem', 'SystemAddress', 'BodyName', 'BodyID', 'DistanceFromArrivalLS', 'StarType', 'Subclass', 'StellarMass', 'Radius', 'AbsoluteMagnitude', 'Age_MY', 'SurfaceTemperature', 'Luminosity', 'SemiMajorAxis', 'Eccentricity', 'OrbitalInclination', 'Periapsis', 'OrbitalPeriod', 'RotationPeriod', 'AxialTilt', 'Rings', 'Parents', 'PlanetClass', 'Atmosphere', 'AtmosphereType', 'AtmosphereComposition', 'Volcanism', 'MassEM', 'SurfaceGravity', 'SurfacePressure', 'Landable', 'Materials', 'Composition', 'ReserveLevel', 'TerraformState', 'WasDiscovered', 'WasMapped'],
  ScanOrganic: ['ScanType', 'Genus', 'Genus_Localised', 'Species', 'Species_Localised', 'Variant', 'Variant_Localised', 'StarSystem', 'SystemName', 'SystemAddress', 'Body', 'BodyID', 'BodyName'],
  Screenshot: ['Filename', 'Width', 'Height', 'System', 'SystemAddress', 'Body', 'BodyID', 'Latitude', 'Longitude', 'Altitude', 'Heading'],
  SellExplorationData: ['Systems', 'Discovered', 'BaseValue', 'Bonus'],
  SellOrganicData: ['MarketID', 'BioData'],
  Touchdown: ['StarSystem', 'SystemAddress', 'Body', 'BodyID', 'BodyName', 'Latitude', 'Longitude', 'PlayerControlled', 'NearestDestination', 'NearestDestination_Localised'],
};

const PRIVACY_BOUNDARY = {
  strip_before_network: true,
  raw_file_uploaded: false,
  allowlist_only: true,
};

const EMPTY_STATE: JournalParserState = {
  system_id64: null,
  system_name: null,
  body_id: null,
  body_name: null,
  commander: null,
  game_version: null,
  game_build: null,
};

interface StreamLine {
  text: string;
  start_offset: number;
  end_offset: number;
  line_number: number;
}

interface FileLike {
  name: string;
  size: number;
  slice(start?: number, end?: number): Blob;
}

export async function parseJournalFilesStreaming(
  sources: readonly JournalFileSource[],
  onFileProgress?: (progress: JournalParseFileProgress) => void,
): Promise<JournalImportParseResult> {
  if (sources.length === 0) throw new Error('Select at least one journal file first.');

  const observations: JournalImportObservationInput[] = [];
  const powerplayEvents: PowerplayJournalEventInput[] = [];
  const checkpoints: JournalFileCheckpoint[] = [];
  const seenKeys = new Set<string>();
  const seenPowerplayKeys = new Set<string>();
  const eventCounts: Record<string, number> = {};
  const manifestFiles: Array<{ name: string; event_count: number }> = [];
  const fileManifest: JournalFileManifestEntry[] = [];
  let linesRead = 0;
  let skippedLines = 0;

  for (let sourceIndex = 0; sourceIndex < sources.length; sourceIndex += 1) {
    // A folder may contain unrelated commanders or out-of-order sessions.
    // Restore context only from this file's explicit checkpoint.
    let state = cloneState(EMPTY_STATE);
    const source = sources[sourceIndex] as JournalFileSource;
    const input = normaliseFileInput(source);
    const file = input.file as FileLike;
    const checkpoint = input.checkpoint;
    const offset = input.offset ?? checkpoint?.next_offset ?? 0;
    const endOffset = Math.min(input.end_offset ?? file.size, file.size);
    if (!Number.isSafeInteger(offset) || offset < 0 || offset > file.size || endOffset < offset) {
      throw new Error(`Invalid journal byte range for ${file.name}.`);
    }
    if (checkpoint) {
      if (checkpoint.version !== 1) throw new Error(`Unsupported checkpoint version for ${file.name}.`);
      if (input.offset != null && input.offset !== checkpoint.next_offset) {
        throw new Error(`Checkpoint offset does not match the requested offset for ${file.name}.`);
      }
      state = cloneState(checkpoint.state);
    }

    let nextOffset = offset;
    let lineNumber = checkpoint?.line_number ?? 0;
    let lastRecordHash = checkpoint?.last_record_hash ?? null;
    let fileEventCount = 0;
    const parseFinalLine = endOffset === file.size;

    for await (const line of streamLines(file, offset, endOffset, lineNumber, parseFinalLine)) {
      nextOffset = line.end_offset;
      lineNumber = line.line_number;
      const trimmed = line.text.trim();
      if (!trimmed) continue;
      linesRead += 1;

      let raw: Record<string, unknown>;
      try {
        raw = parseJournalJson(trimmed);
      } catch {
        skippedLines += 1;
        continue;
      }

      const recordHash = await sha256Hex(new TextEncoder().encode(trimmed));
      lastRecordHash = recordHash;
      const powerplayEvent = normalisePowerplayEvent(raw, recordHash, state);
      const observation = normaliseObservation(raw, file.name, line.start_offset, recordHash, state);
      updateState(state, raw);
      if (powerplayEvent && !seenPowerplayKeys.has(recordHash)) {
        seenPowerplayKeys.add(recordHash);
        powerplayEvents.push(powerplayEvent);
      }
      if (!observation || seenKeys.has(recordHash)) {
        if (!powerplayEvent) skippedLines += 1;
        continue;
      }

      seenKeys.add(recordHash);
      observations.push(observation);
      fileEventCount += 1;
      eventCounts[observation.event_type] = (eventCounts[observation.event_type] ?? 0) + 1;
    }

    manifestFiles.push({ name: file.name, event_count: fileEventCount });
    // File-level provenance for the V3 lane: SHA-256 over the whole raw
    // file (content-addressed identity — filename and parse range never
    // participate), plus size. Computed in this worker where the bytes are
    // already being streamed.
    const contentSha256 = await fileSha256Hex(file);
    fileManifest.push({
      name: file.name,
      content_sha256: contentSha256,
      size_bytes: file.size,
      event_count: fileEventCount,
      line_count: lineNumber,
    });
    checkpoints.push({
      version: 1,
      source_file: file.name,
      source_size: file.size,
      next_offset: nextOffset,
      line_number: lineNumber,
      last_record_hash: lastRecordHash,
      complete: nextOffset >= file.size,
      state: cloneState(state),
    });
    onFileProgress?.({
      files_processed: sourceIndex + 1,
      files_total: sources.length,
      events_parsed: observations.length,
    });
  }

  return {
    client_manifest: {
      parser_version: JOURNAL_PARSER_VERSION,
      files: manifestFiles,
    },
    file_manifest: fileManifest,
    observations,
    powerplay_events: powerplayEvents,
    preview: {
      files_processed: sources.length,
      lines_read: linesRead,
      observations_ready: observations.length,
      skipped_lines: skippedLines,
      powerplay_events_ready: powerplayEvents.length,
      event_counts: eventCounts,
    },
    checkpoints,
  };
}

function normalisePowerplayEvent(
  raw: Record<string, unknown>,
  sourceRecordHash: string,
  state: JournalParserState,
): PowerplayJournalEventInput | null {
  const eventType = textValue(raw.event);
  const observedAt = textValue(raw.timestamp);
  if (!eventType || !observedAt || !POWERPLAY_EVENT_SET.has(eventType)) return null;
  const systemEvent = eventType === 'Location' || eventType === 'FSDJump';
  if (systemEvent && !POWERPLAY_SYSTEM_FIELDS.some((field) => field in raw)) return null;

  const payload: Record<string, unknown> = { event: eventType, timestamp: observedAt };
  const fields = systemEvent
    ? ['StarSystem', 'SystemAddress', ...POWERPLAY_SYSTEM_FIELDS]
    : POWERPLAY_PERSONAL_FIELDS;
  for (const field of fields) {
    // Presence matters: an explicit null is evidence and must not collapse
    // into the journal field being absent.
    if (field in raw) payload[field] = raw[field];
  }
  return {
    observation_key: sourceRecordHash,
    event_type: eventType,
    observed_at: observedAt,
    game_build: [state.game_version, state.game_build].filter(Boolean).join(' / ') || null,
    source_payload: toJournalTransportValue(payload) as Record<string, unknown>,
  };
}

function normaliseObservation(
  raw: Record<string, unknown>,
  sourceFile: string,
  sourceOffset: number,
  sourceRecordHash: string,
  state: JournalParserState,
): JournalImportObservationInput | null {
  const eventType = typeof raw.event === 'string' ? raw.event as JournalEventType : null;
  if (!eventType || !SUPPORTED_EVENT_SET.has(eventType)) return null;

  const routeItems = eventType === 'NavRoute' && Array.isArray(raw.Route) ? raw.Route : [];
  const firstRouteItem = routeItems.find((item) => item && typeof item === 'object') as Record<string, unknown> | undefined;
  const directSystemId = decimalString(raw.SystemAddress) ?? decimalString(firstRouteItem?.SystemAddress);
  const systemId64 = eventType === 'NavRouteClear' ? null : directSystemId ?? state.system_id64;
  const directBodyId = decimalString(raw.BodyID ?? raw.Body, { allowZero: true });
  const bodyId = directBodyId ?? (BODY_EVENTS.has(eventType) ? state.body_id : null);
  const bodyName = textValue(raw.BodyName)
    ?? textValue(raw.Body)
    ?? (BODY_EVENTS.has(eventType) ? state.body_name : null);
  const systemName = textValue(raw.StarSystem)
    ?? textValue(raw.System)
    ?? (eventType === 'FSDTarget' ? textValue(raw.Name) : null)
    ?? textValue(firstRouteItem?.StarSystem)
    ?? state.system_name;
  const subjectType = eventType === 'NavRoute' || eventType === 'NavRouteClear'
    ? 'route'
    : BODY_EVENTS.has(eventType) && (bodyId != null || bodyName != null)
      ? 'body'
      : 'system';
  const payload = payloadForEvent(eventType, raw, state);

  return {
    observation_key: sourceRecordHash,
    source_file: sourceFile,
    source_offset: sourceOffset,
    event_type: eventType,
    observed_at: textValue(raw.timestamp),
    system_id64: systemId64,
    system_name: systemName,
    subject_type: subjectType,
    subject_id: subjectType === 'body' ? bodyId ?? bodyName : null,
    summary: summaryForEvent(eventType, raw),
    payload,
    privacy_boundary: PRIVACY_BOUNDARY,
  };
}

function payloadForEvent(
  eventType: JournalEventType,
  raw: Record<string, unknown>,
  state: JournalParserState,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  for (const key of EVENT_PAYLOAD_FIELDS[eventType]) {
    if (raw[key] !== undefined && raw[key] !== null) payload[key] = raw[key];
  }

  if (eventType === 'Screenshot' && typeof payload.Filename === 'string') {
    payload.Filename = payload.Filename.split(/[\\/]/).pop() ?? payload.Filename;
  }
  if (state.game_version && payload.gameversion == null) payload.gameversion = state.game_version;
  if (state.game_build && payload.gamebuild == null && payload.build == null) payload.gamebuild = state.game_build;
  // V3 lane: attach the capitalized GameVersion/GameBuild contract fields
  // to observation payloads from the rolling Fileheader/LoadGame state.
  if (GAME_VERSION_ATTACH_EVENTS.has(eventType)) {
    if (state.game_version && payload.GameVersion == null) payload.GameVersion = state.game_version;
    if (state.game_build && payload.GameBuild == null) payload.GameBuild = state.game_build;
  }
  return toJournalTransportValue(payload) as Record<string, unknown>;
}

function updateState(state: JournalParserState, raw: Record<string, unknown>): void {
  const eventType = textValue(raw.event);
  const systemId64 = decimalString(raw.SystemAddress);
  const systemName = textValue(raw.StarSystem) ?? textValue(raw.System);
  if (eventType !== 'FSDTarget') {
    if (systemId64) state.system_id64 = systemId64;
    if (systemName) state.system_name = systemName;
  }

  if (eventType === 'FSDJump' || eventType === 'CarrierJump') {
    state.body_id = null;
    state.body_name = null;
  }

  const bodyId = decimalString(raw.BodyID ?? raw.Body, { allowZero: true });
  const bodyName = textValue(raw.BodyName) ?? textValue(raw.Body);
  if (bodyId != null) state.body_id = bodyId;
  if (bodyName) state.body_name = bodyName;

  if (eventType === 'LeaveBody') {
    state.body_id = null;
    state.body_name = null;
  }

  const commander = eventType === 'LoadGame' ? textValue(raw.Commander)
    : eventType === 'Commander' ? textValue(raw.Name) : null;
  if (commander) state.commander = commander;
  const gameVersion = textValue(raw.gameversion);
  const gameBuild = textValue(raw.build) ?? textValue(raw.gamebuild);
  if (gameVersion) state.game_version = gameVersion;
  if (gameBuild) state.game_build = gameBuild;
}

function summaryForEvent(eventType: JournalEventType, raw: Record<string, unknown>): string {
  const body = textValue(raw.BodyName) ?? 'a body';
  const summaries: Partial<Record<JournalEventType, string>> = {
    ApproachBody: `Approached ${body}.`,
    CarrierJump: 'Carrier jump observed from local journal.',
    CodexEntry: 'Codex entry observed from local journal.',
    Commander: 'Commander identity event observed from local journal.',
    Died: 'Commander death observed from local journal.',
    Disembark: 'Commander disembark observed from local journal.',
    Docked: `Docked at ${textValue(raw.StationName) ?? 'a station'}.`,
    Embark: 'Commander embark observed from local journal.',
    Fileheader: 'Journal file metadata observed.',
    FSDJump: 'Commander jump observed from local journal.',
    FSDTarget: 'Frame-shift target observed from local journal.',
    FSSAllBodiesFound: 'All bodies found event observed from local journal.',
    FSSBodySignals: `Body signals observed for ${body}.`,
    FSSDiscoveryScan: 'Discovery scan observed from local journal.',
    LeaveBody: `Left ${body}.`,
    Liftoff: `Liftoff observed from ${body}.`,
    LoadGame: 'Game session loaded.',
    Location: 'Commander location observed from local journal.',
    MultiSellExplorationData: 'Exploration data batch sale observed.',
    NavRoute: 'Navigation route observed from local journal.',
    NavRouteClear: 'Navigation route cleared.',
    Resurrect: 'Commander resurrection observed from local journal.',
    SAAScanComplete: `Surface mapping completed for ${body}.`,
    SAASignalsFound: `Surface signals observed for ${body}.`,
    Scan: `Body scan observed for ${body}.`,
    ScanOrganic: `Organic scan observed for ${textValue(raw.Species_Localised) ?? textValue(raw.Species) ?? 'an organism'}.`,
    Screenshot: 'Screenshot metadata observed from local journal.',
    SellExplorationData: 'Exploration data sale observed.',
    SellOrganicData: 'Organic data sale observed.',
    Touchdown: `Touchdown observed on ${body}.`,
  };
  return summaries[eventType] ?? `${eventType} observed from local journal.`;
}

async function* streamLines(
  file: FileLike,
  startOffset: number,
  endOffset: number,
  initialLineNumber: number,
  parseFinalLine: boolean,
): AsyncGenerator<StreamLine> {
  const stream = file.slice(startOffset, endOffset).stream();
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let pending = new Uint8Array(0);
  let pendingStart = startOffset;
  let lineNumber = initialLineNumber;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const combined = new Uint8Array(pending.length + value.length);
      combined.set(pending);
      combined.set(value, pending.length);
      let lineStart = 0;
      for (let index = 0; index < combined.length; index += 1) {
        if (combined[index] !== 0x0a) continue;
        const contentEnd = index > lineStart && combined[index - 1] === 0x0d ? index - 1 : index;
        lineNumber += 1;
        yield {
          text: decoder.decode(combined.subarray(lineStart, contentEnd)),
          start_offset: pendingStart + lineStart,
          end_offset: pendingStart + index + 1,
          line_number: lineNumber,
        };
        lineStart = index + 1;
      }
      pendingStart += lineStart;
      pending = combined.slice(lineStart);
    }

    if (parseFinalLine && pending.length > 0) {
      lineNumber += 1;
      yield {
        text: decoder.decode(pending),
        start_offset: pendingStart,
        end_offset: pendingStart + pending.length,
        line_number: lineNumber,
      };
    }
  } finally {
    reader.releaseLock();
  }
}

async function sha256Hex(value: Uint8Array): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', value as BufferSource);
  return Array.from(new Uint8Array(digest))
    .map((item) => item.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * SHA-256 of a whole file, read in stream chunks and digested once via
 * WebCrypto. Chunked over the raw bytes so memory stays bounded to the file
 * size (up to the 128 MiB client cap) rather than an unbounded accumulation;
 * the digest itself is a single `crypto.subtle.digest` over the assembled
 * ArrayBuffer (WebCrypto has no incremental digest API). The hash is always
 * over `slice(0, size)` — the full file, independent of any incremental
 * parse range — so it is a stable content-addressable identity.
 */
async function fileSha256Hex(file: FileLike): Promise<string> {
  const byteLength = file.size;
  const combined = new Uint8Array(byteLength);
  const reader = file.slice(0, byteLength).stream().getReader();
  let offset = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) {
        combined.set(value, offset);
        offset += value.length;
      }
    }
  } finally {
    reader.releaseLock();
  }
  return sha256Hex(combined);
}

function normaliseFileInput(source: JournalFileSource): JournalFileInput {
  if ('file' in source) return source;
  return { file: source };
}

function textValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function cloneState(state: JournalParserState): JournalParserState {
  return { ...state };
}
