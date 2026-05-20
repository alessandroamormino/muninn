/**
 * fetchJson — HTTP client with optional Authorization header injection.
 *
 * createApiClient(token, on401): factory that returns a fetchJson-compatible
 * function. Adds `Authorization: Bearer <token>` when token is non-null.
 * On HTTP 401: calls on401() (clears AuthContext + triggers redirect) then re-throws.
 *
 * The legacy fetchJson export is kept for backward compatibility with callers
 * that do not yet use the factory (unauthenticated endpoints like /health).
 */
export function createApiClient(
  token: string | null,
  on401: () => void,
) {
  return async function fetchJson<T>(url: string, init: RequestInit = {}): Promise<T> {
    const headers: Record<string, string> = {
      ...(init.headers as Record<string, string> ?? {}),
    }
    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }
    const res = await fetch(url, { ...init, headers })
    if (res.status === 401) {
      on401()
      const text = await res.text().catch(() => '')
      throw new Error(`HTTP 401: ${text || res.statusText}`)
    }
    if (!res.ok) {
      const text = await res.text().catch(() => '')
      throw new Error(`HTTP ${res.status}: ${text || res.statusText}`)
    }
    return res.json() as Promise<T>
  }
}

/** Legacy single-call helper for unauthenticated requests (e.g. /auth/login). */
export async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status}: ${text || res.statusText}`)
  }
  return res.json() as Promise<T>
}
