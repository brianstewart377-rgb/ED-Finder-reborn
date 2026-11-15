// A person's own journals: their commanders, what was imported, and what
// they have offered to the shared galaxy.
// Offered to the app through ./client, like everything in this folder.
import {
  ADMIN_TOKEN_SESSION_KEY,
  ApiError,
  LEGACY_ADMIN_ENDPOINTS,
  adminEndpointClass,
  apiRequest,
  canonicalApiPath,
} from '@ed-finder/api-client/core';
import type { V3JournalImportRequest } from './generated';
import {
  createVerifiedJournalImport,
  getJournalGalaxyImpact,
  getV3JournalSummary,
  listJournalGalaxyContributions,
  listVerifiedCommanders,
  offerJournalGalaxyFacts,
  withdrawJournalGalaxyContribution,
} from './generated/sdk.gen';

export const getVerifiedCommanders = async (signal?: AbortSignal) =>
  (await listVerifiedCommanders({ throwOnError: true, signal })).data;

/** Account-wide import metadata distinguishes no journals from an empty viewport. */
export const getCommanderJournalSummary = async (signal?: AbortSignal) =>
  (await getV3JournalSummary({ throwOnError: true, signal })).data;

export const getGalaxyImpact = async (signal?: AbortSignal) =>
  (await getJournalGalaxyImpact({ throwOnError: true, signal })).data;

export const importVerifiedJournals = async (
  body: V3JournalImportRequest,
  signal?: AbortSignal,
) =>
  (await createVerifiedJournalImport({ body, throwOnError: true, signal }))
    .data;

export const offerGalaxyFacts = async (
  importId: string,
  fileSha256: string[],
  signal?: AbortSignal,
) =>
  (
    await offerJournalGalaxyFacts({
      path: { import_id: importId },
      body: {
        policy_version: 'journal-galaxy-physical-v1',
        share_on_site_and_api: true,
        file_sha256: fileSha256,
      },
      throwOnError: true,
      signal,
    })
  ).data;

export const getGalaxyContributions = async (
  offset = 0,
  signal?: AbortSignal,
) =>
  (
    await listJournalGalaxyContributions({
      query: { offset, limit: 50 },
      throwOnError: true,
      signal,
    })
  ).data;

export const withdrawGalaxyContribution = async (
  id: string,
  signal?: AbortSignal,
) =>
  (
    await withdrawJournalGalaxyContribution({
      path: { contribution_id: id },
      throwOnError: true,
      signal,
    })
  ).data;

export {
  ADMIN_TOKEN_SESSION_KEY,
  ApiError,
  LEGACY_ADMIN_ENDPOINTS,
  adminEndpointClass,
  apiRequest,
  canonicalApiPath,
};
