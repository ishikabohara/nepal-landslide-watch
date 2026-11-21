const API_BASE = import.meta.env.VITE_API_BASE || '/api';

export async function api<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { signal });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === 'string') message = body.detail;
      else if (typeof body.message === 'string') message = body.message;
    } catch { /* Keep the HTTP status if an error has no JSON body. */ }
    throw new Error(message);
  }
  const type = response.headers.get('content-type') || '';
  if (!type.includes('json')) throw new Error('The data service did not return JSON. Check that the API is running.');
  return response.json() as Promise<T>;
}

export function query(values: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined) params.set(key, String(value));
  });
  return params.toString();
}
