/** Catalogue values may already include their human-readable Star suffix. */
export function starClassLabel(value: string): string {
  return /star$/i.test(value) ? value : `${value} star`;
}
