'use client'

import { useQuery } from '@tanstack/react-query'
import { useAuth, type RefreshOutcome } from '@components/Contexts/AuthContext'
import { dispatchAuthRefreshed } from '@/lib/auth/events'

type RefreshSession = () => Promise<RefreshOutcome>
// Only the existing provider's refresh callback is retained here. Tokens,
// session state, and refresh deduplication stay with AuthContext.
let workspaceRefresh: RefreshSession | undefined
export function bindTTSessionRefresh(refresh: RefreshSession) {
  workspaceRefresh = refresh
  return () => { if (workspaceRefresh === refresh) workspaceRefresh = undefined }
}

export class TTApiError extends Error {
  public status: number
  public authFailure?: 'terminal' | 'transient'
  constructor(status: number, message: string, authFailure?: 'terminal' | 'transient') {
    super(message); this.status = status; this.authFailure = authFailure; this.name = 'TTApiError'
  }
}

export interface TTBootstrap {
  name: string
  org_id: number
  user: { id: string; name: string; email_verified: boolean }
  is_staff: boolean
  permissions: string[]
  source_url: string
}

export async function ttApi<T>(path: string, init: RequestInit = {}, refresh = workspaceRefresh): Promise<T> {
  const url = `/api/v1/tt/${path.replace(/^\//, '')}`
  const headers = new Headers(init.headers)
  if (init.body && !(typeof FormData !== 'undefined' && init.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  // This transport uses the provider's tri-state refresh outcome. Prevent the
  // generic interceptor from also retrying or treating a refresh outage as logout.
  headers.set('X-LH-Auth-Retry', '1')
  const options: RequestInit = {
    ...init, credentials: 'include', cache: 'no-store',
    headers,
  }
  let response = await fetch(url, options)
  if (response.status === 401 && refresh) {
    let outcome: RefreshOutcome
    try { outcome = await refresh() } catch { outcome = { status: 'transient' } }
    if (outcome.status === 'transient') throw new TTApiError(503, 'Your session could not be refreshed right now. Please try again.', 'transient')
    if (outcome.status === 'unauthenticated') throw new TTApiError(401, 'Please sign in to continue.', 'terminal')
    dispatchAuthRefreshed({ access_token: outcome.access_token, expiry: outcome.expiry })
    const retryHeaders = new Headers(headers)
    retryHeaders.set('Authorization', `Bearer ${outcome.access_token}`)
    // Only an explicit rejection is replayed. Network errors and server errors
    // never replay a mutation, and its original JSON/idempotency key is preserved.
    response = await fetch(url, { ...options, headers: retryHeaders })
  }
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = body?.detail
    const message = typeof detail === 'string' ? detail : detail?.message || (Array.isArray(detail?.errors) ? detail.errors.join(' ') : undefined)
    throw new TTApiError(response.status, message || (response.status === 401 ? 'Please sign in to continue.' : 'We could not complete this step. Please try again.'), response.status === 401 ? 'terminal' : undefined)
  }
  return body as T
}

export function useTTQuery<T>(path: string, enabled = true) {
  const { refreshAccessToken } = useAuth()
  return useQuery({ queryKey: ['thinktogether', path], queryFn: () => ttApi<T>(path, {}, refreshAccessToken), enabled,
    retry: (count, error) => !(error instanceof TTApiError && (error.status < 500 || error.authFailure === 'transient')) && count < 1 })
}

export function ttPost<T>(path: string, body: unknown = {}) {
  return ttApi<T>(path, { method: 'POST', body: JSON.stringify(body) })
}
