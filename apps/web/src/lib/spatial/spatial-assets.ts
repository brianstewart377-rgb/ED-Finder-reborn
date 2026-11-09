/**
 * Reviewed static spatial assets are not ordinary API operations, so they stay
 * outside the generated SDK facade. They are still fetched through one
 * same-origin, validated helper so app components never call fetch directly.
 */

function canonicalAssetPath(path: string): string {
  if (
    /^[A-Za-z][A-Za-z\d+.-]*:/u.test(path) ||
    path.startsWith('//') ||
    path.includes('\\')
  )
    throw new TypeError('Static asset paths must be same-origin');

  const rooted = path.startsWith('/') ? path : `/${path}`;
  const normalised = new URL(rooted, 'https://ed-finder.invalid');
  if (normalised.origin !== 'https://ed-finder.invalid') {
    throw new TypeError('Static asset paths must be same-origin');
  }
  return `${normalised.pathname}${normalised.search}${normalised.hash}`;
}

export async function fetchStaticAsset(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const assetPath = canonicalAssetPath(path);
  let response: Response;
  try {
    response = await fetch(assetPath, init);
  } catch (cause) {
    if (
      cause &&
      typeof cause === 'object' &&
      'name' in cause &&
      cause.name === 'AbortError'
    )
      throw cause;
    throw new Error(`Static asset request failed on ${assetPath}`, { cause });
  }
  if (!response.ok) {
    throw new Error(
      `Static asset request failed with HTTP ${response.status} on ${assetPath}`,
    );
  }
  return response;
}
