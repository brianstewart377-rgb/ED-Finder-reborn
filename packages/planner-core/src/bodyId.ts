export type BodyIdValue = string | number | null | undefined;

const LARGE_NUMERIC_ID = /^\d{16,}$/;

export function bodyIdKey(value: BodyIdValue): string {
  if (value == null) return '';
  const raw = String(value);
  if (LARGE_NUMERIC_ID.test(raw)) {
    const rounded = Number(raw);
    if (Number.isFinite(rounded)) return String(rounded);
  }
  return raw;
}

export function sameBodyId(left: BodyIdValue, right: BodyIdValue): boolean {
  const leftKey = bodyIdKey(left);
  return leftKey.length > 0 && leftKey === bodyIdKey(right);
}
