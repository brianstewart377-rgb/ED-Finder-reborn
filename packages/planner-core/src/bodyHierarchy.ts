import type { SystemBody } from '@ed-finder/api-client/types';

interface ParsedBodyKey {
  group: number;
  parts: number[];
}

function bodyName(body: SystemBody): string | null {
  return typeof body.name === 'string' && body.name.trim() ? body.name.trim() : null;
}

export function bodyHierarchySortKey(body: SystemBody, systemName?: string | null): string | null {
  const apiKey = typeof body.body_sort_key === 'string' ? body.body_sort_key : null;
  if (apiKey) return apiKey;
  const parsed = parseBodyHierarchyKey(bodyName(body), systemName);
  return parsed ? keyToString(parsed) : null;
}

export function compareBodiesByHierarchy(a: SystemBody, b: SystemBody, systemName?: string | null): number {
  const parsedA = parseSortKey(bodyHierarchySortKey(a, systemName));
  const parsedB = parseSortKey(bodyHierarchySortKey(b, systemName));
  if (parsedA && parsedB) return compareParsedKeys(parsedA, parsedB);
  if (parsedA && !parsedB) return -1;
  if (!parsedA && parsedB) return 1;
  return 0;
}

export function sortByBodyHierarchy<T>(
  items: T[],
  bodyForItem: (item: T) => SystemBody,
  systemName?: string | null,
): T[] {
  return items
    .map((item, index) => ({ item, index }))
    .sort((left, right) => {
      const bodyOrder = compareBodiesByHierarchy(bodyForItem(left.item), bodyForItem(right.item), systemName);
      if (bodyOrder !== 0) return bodyOrder;
      return left.index - right.index;
    })
    .map(({ item }) => item);
}

function parseBodyHierarchyKey(name: string | null, systemName?: string | null): ParsedBodyKey | null {
  if (!name) return null;
  const suffix = bodySuffix(name, systemName);
  if (suffix == null) return null;
  if (suffix === '') return { group: 0, parts: [0] };

  const tokens = suffix.split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return { group: 0, parts: [0] };

  let starPrefix = 0;
  if (/^[A-Z]+$/.test(tokens[0])) {
    starPrefix = lettersValue(tokens.shift() ?? '', 'A');
    if (tokens.length === 0) return { group: 0, parts: [starPrefix] };
  }

  const first = tokens.shift();
  if (!first || !/^\d+$/.test(first)) return null;

  const parts = [starPrefix, Number.parseInt(first, 10)];
  for (const token of tokens) {
    if (!/^[a-z]+$/.test(token)) return null;
    parts.push(lettersValue(token, 'a'));
  }
  return { group: 1, parts };
}

function bodySuffix(name: string, systemName?: string | null): string | null {
  const system = systemName?.trim();
  if (system) {
    if (name === system) return '';
    const prefix = `${system} `;
    return name.startsWith(prefix) ? name.slice(prefix.length).trim() : null;
  }

  const tokens = name.split(/\s+/).filter(Boolean);
  const firstNumber = tokens.findIndex((token) => /^\d+$/.test(token));
  if (firstNumber === -1) return '';
  return tokens.slice(firstNumber).join(' ');
}

function parseSortKey(value: string | null): ParsedBodyKey | null {
  if (!value) return null;
  const [groupText, partsText = ''] = value.split(':', 2);
  const group = Number.parseInt(groupText, 10);
  if (!Number.isFinite(group)) return null;
  const parts = partsText
    ? partsText.split('.').map((part) => Number.parseInt(part, 10))
    : [];
  if (parts.some((part) => !Number.isFinite(part))) return null;
  return { group, parts };
}

function compareParsedKeys(a: ParsedBodyKey, b: ParsedBodyKey): number {
  if (a.group !== b.group) return a.group - b.group;
  const length = Math.min(a.parts.length, b.parts.length);
  for (let index = 0; index < length; index += 1) {
    if (a.parts[index] !== b.parts[index]) return a.parts[index] - b.parts[index];
  }
  return a.parts.length - b.parts.length;
}

function keyToString(key: ParsedBodyKey): string {
  return `${key.group}:${key.parts.map((part) => String(part).padStart(6, '0')).join('.')}`;
}

function lettersValue(value: string, base: 'A' | 'a'): number {
  const offset = base.charCodeAt(0) - 1;
  return value.split('').reduce((result, character) => (
    result * 26 + character.charCodeAt(0) - offset
  ), 0);
}
