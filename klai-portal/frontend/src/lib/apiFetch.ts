import { API_BASE } from '@/lib/api'

export class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(`${status}: ${detail}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

interface ApiFetchOptions extends Omit<RequestInit, 'headers'> {
  headers?: Record<string, string>
}

export async function apiFetch<T>(
  path: string,
  token: string | undefined,
  options: ApiFetchOptions = {},
): Promise<T> {
  const { headers: extraHeaders, ...rest } = options
  const headers: Record<string, string> = {
    ...extraHeaders,
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  if (rest.body && typeof rest.body === 'string') {
    headers['Content-Type'] ??= 'application/json'
  }

  const res = await fetch(`${API_BASE}${path}`, { headers, ...rest })
  if (!res.ok) {
    let detail = `${res.status}`
    try {
      const body = (await res.json()) as { detail?: string | object[] }
      if (body.detail) {
        detail = typeof body.detail === 'string'
          ? body.detail
          : JSON.stringify(body.detail)
      }
    } catch {
      // no JSON body
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}
