import { afterAll, beforeEach, describe, expect, mock, test } from 'bun:test'
import { readFileSync } from 'node:fs'

mock.module('@components/Contexts/AuthContext', () => ({ useAuth: () => ({ refreshAccessToken: async () => ({ status: 'unauthenticated' }) }) }))
const refreshed = []
mock.module('@/lib/auth/events', () => ({ dispatchAuthRefreshed: value => refreshed.push(value) }))
const { bindTTSessionRefresh, ttApi, TTApiError } = await import('../services/thinktogether/api.ts')
const originalFetch = globalThis.fetch
const calls = []
let responses = []
globalThis.fetch = async (url, init) => {
  calls.push({ url, init })
  const result = responses.shift()
  if (result instanceof Error) throw result
  if (!result) throw new Error('Unexpected extra request')
  return new Response(JSON.stringify(result.body || {}), { status: result.status, headers: { 'Content-Type': 'application/json' } })
}
afterAll(() => { globalThis.fetch = originalFetch })
beforeEach(() => { calls.length = 0; refreshed.length = 0; responses = [] })

describe('ThinkTogether session recovery', () => {
  test('retries an expired-access mutation once and preserves its body and retry key', async () => {
    responses = [{ status: 401 }, { status: 200, body: { id: 'saved-attempt' } }]
    const refresh = mock(async () => ({ status: 'ok', access_token: 'fresh-access', expiry: 5000 }))
    const body = JSON.stringify({ response: { choice: 'a' }, idempotency_key: 'same-request' })
    const result = await ttApi('attempts', { method: 'POST', body, headers: new Headers({ 'X-Custom': 'kept' }) }, refresh)
    expect(result).toEqual({ id: 'saved-attempt' })
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(calls).toHaveLength(2)
    expect(calls[0].init.body).toBe(body)
    expect(calls[1].init.body).toBe(body)
    expect(calls[1].init.method).toBe('POST')
    expect(calls[1].init.credentials).toBe('include')
    expect(calls[1].init.cache).toBe('no-store')
    expect(calls[1].init.headers.get('Authorization')).toBe('Bearer fresh-access')
    expect(calls[1].init.headers.get('X-LH-Auth-Retry')).toBe('1')
    expect(calls[1].init.headers.get('X-Custom')).toBe('kept')
    expect(refreshed).toEqual([{ access_token: 'fresh-access', expiry: 5000 }])
  })
  test('reports a rejected refresh as terminal without replaying a mutation', async () => {
    responses = [{ status: 401 }]
    const refresh = mock(async () => ({ status: 'unauthenticated' }))
    let failure
    try { await ttApi('bootstrap', {}, refresh) } catch (error) { failure = error }
    expect(failure).toBeInstanceOf(TTApiError)
    expect(failure.status).toBe(401)
    expect(failure.authFailure).toBe('terminal')
    expect(calls).toHaveLength(1)
    expect(refreshed).toHaveLength(0)
  })
  test('keeps a refresh outage separate from terminal sign out', async () => {
    responses = [{ status: 401 }]
    const refresh = mock(async () => ({ status: 'transient' }))
    let failure
    try { await ttApi('bootstrap', {}, refresh) } catch (error) { failure = error }
    expect(failure.status).toBe(503)
    expect(failure.authFailure).toBe('transient')
    expect(calls).toHaveLength(1)
    expect(refreshed).toHaveLength(0)
  })
  test('treats an unexpected refresh network failure as transient', async () => {
    responses = [{ status: 401 }]
    let failure
    try { await ttApi('bootstrap', {}, async () => { throw new TypeError('offline') }) } catch (error) { failure = error }
    expect(failure.status).toBe(503)
    expect(failure.authFailure).toBe('transient')
    expect(calls).toHaveLength(1)
  })
  test('never loops if the refreshed access credential is also rejected', async () => {
    responses = [{ status: 401 }, { status: 401 }]
    const refresh = mock(async () => ({ status: 'ok', access_token: 'rejected-access' }))
    let failure
    try { await ttApi('bootstrap', {}, refresh) } catch (error) { failure = error }
    expect(failure.status).toBe(401)
    expect(failure.authFailure).toBe('terminal')
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(calls).toHaveLength(2)
  })
  test('does not refresh permission denials or service outages', async () => {
    const refresh = mock(async () => ({ status: 'unauthenticated' }))
    for (const status of [403, 429, 500, 502, 503]) {
      responses = [{ status }]
      let failure
      try { await ttApi('bootstrap', {}, refresh) } catch (error) { failure = error }
      expect(failure.status).toBe(status)
      expect(failure.authFailure).toBeUndefined()
    }
    expect(refresh).toHaveBeenCalledTimes(0)
    expect(calls).toHaveLength(5)
  })
  test('never replays a mutation after a network failure', async () => {
    responses = [new TypeError('offline')]
    const refresh = mock(async () => ({ status: 'ok', access_token: 'access' }))
    await expect(ttApi('attempts', { method: 'POST', body: '{}' }, refresh)).rejects.toThrow('offline')
    expect(refresh).toHaveBeenCalledTimes(0)
    expect(calls).toHaveLength(1)
  })
  test('uses the mounted provider callback for imperative requests and releases it on unmount', async () => {
    responses = [{ status: 401 }, { status: 200, body: { saved: true } }]
    const refresh = mock(async () => ({ status: 'ok', access_token: 'provider-access' }))
    const cleanup = bindTTSessionRefresh(refresh)
    try { expect(await ttApi('me/profile')).toEqual({ saved: true }) } finally { cleanup() }
    expect(refresh).toHaveBeenCalledTimes(1)
    responses = [{ status: 401 }]
    await expect(ttApi('bootstrap')).rejects.toHaveProperty('status', 401)
    expect(refresh).toHaveBeenCalledTimes(1)
  })
  test('workspace recovery delegates cookie clearing and navigation to existing signOut', () => {
    const shell = readFileSync(new URL('../components/ThinkTogether/Shell.tsx', import.meta.url), 'utf8')
    expect(shell).toContain("query.error.authFailure === 'terminal'")
    expect(shell).toContain('await signOut({ callbackUrl: loginHref, redirect: true })')
    expect(shell).not.toContain('router.replace(')
    expect(shell).toContain("'Sign in'")
  })
})
