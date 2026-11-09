import type { RatingsIdentity } from '$lib/api/client';
import type { Id64 } from '$lib/domain/id64';

export const AVAILABILITY_LABELS = {
  unknown: 'Unknown',
  'not-integrated': 'Not integrated yet',
  unavailable: 'Temporarily unavailable',
} as const;

export type Availability = keyof typeof AVAILABILITY_LABELS;

/** Metadata from the ratings query already used by Inspect; never a new request. */
export type RatingsSourceState = Readonly<{
  id64: Id64;
  status: 'loading' | 'available' | 'unknown' | 'unavailable';
  identity: RatingsIdentity | null;
  retry: () => void;
}>;

export function sourceDate(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return (
    new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: 'UTC',
    }).format(date) + ' UTC'
  );
}
