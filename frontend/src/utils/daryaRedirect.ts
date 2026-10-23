const DARYA_REDIRECT_ORIGIN = 'https://darya.invalid';

export function canonicalDaryaRedirect(value: string | null | undefined): string | null {
  if (!value || !value.startsWith('/') || value.startsWith('//')) return null;
  try {
    const parsed = new URL(value, DARYA_REDIRECT_ORIGIN);
    if (parsed.origin !== DARYA_REDIRECT_ORIGIN || parsed.pathname !== '/darya') return null;
    return `/darya${parsed.search}${parsed.hash}`;
  } catch {
    return null;
  }
}
