import { afterAll, beforeEach, describe, expect, mock, test } from 'bun:test'
import { NextRequest } from 'next/server'

mock.module('./services/config/config', () => ({ getAPIUrl: () => 'http://api.test/' }))
mock.module('@services/config/config', () => ({ getAPIUrl: () => 'http://api.test/' }))
const savedFetch = globalThis.fetch
const savedFlag = process.env.NEXT_PUBLIC_THINKTOGETHER
globalThis.fetch = async () => Response.json({ tenancy: 'single', mode: 'oss', default_org_slug: 'school', frontend_domain: 'school.test', top_domain: 'school.test' })
afterAll(() => {
  globalThis.fetch = savedFetch
  if (savedFlag === undefined) delete process.env.NEXT_PUBLIC_THINKTOGETHER
  else process.env.NEXT_PUBLIC_THINKTOGETHER = savedFlag
})
const { default: proxy } = await import('../proxy.ts')
const get = path => proxy(new NextRequest(`https://school.test${path}`, { headers: { host: 'school.test' } }))
beforeEach(() => { process.env.NEXT_PUBLIC_THINKTOGETHER = 'true' })

describe('ThinkTogether B2C entry points', () => {
  test('legacy progress and path entry points resolve to the personal program', async () => {
    for (const [from, to] of [['/paths', '/program'], ['/path/old-path', '/program'], ['/trail', '/progress']]) {
      const response = await get(from)
      expect(response.status).toBe(307)
      expect(response.headers.get('location')).toBe(`https://school.test/thinktogether${to}`)
    }
  })
  test('standalone social and meeting catalogs resolve to My group', async () => {
    for (const from of ['/communities', '/boards', '/live', '/live/old-session/room']) {
      expect((await get(from)).headers.get('location')).toBe('https://school.test/thinktogether/group')
    }
  })
  test('all legacy purchase entry points resolve to the one Polar access page', async () => {
    for (const from of ['/store', '/store/offers/offer-old', '/account/purchases', '/billing', '/subscriptions', '/payments/stripe/connect/oauth']) {
      expect((await get(from)).headers.get('location')).toBe('https://school.test/thinktogether/billing')
    }
  })
  test('organization creation and switching do not open another academy', async () => {
    for (const from of ['/new', '/organizations']) {
      expect((await get(from)).headers.get('location')).toBe('https://school.test/thinktogether/today')
    }
  })
  test('Home opens the organization landing instead of returning to Today', async () => {
    const root = await get('/')
    expect(root.headers.get('location')).toBeNull()
    expect(root.headers.get('x-middleware-rewrite')).toBe('https://school.test/orgs/school/')
    expect((await get('/home?from=menu')).headers.get('location')).toBe('https://school.test/?from=menu')
    expect((await get('/orgs/school/home')).headers.get('location')).toBe('https://school.test/orgs/school/')
  })
  test('explicit organization paths keep their organization prefix and search parameters', async () => {
    expect((await get('/orgs/school/store?return=profile')).headers.get('location')).toBe('https://school.test/orgs/school/thinktogether/billing?return=profile')
  })
  test('boards embedded in lessons, editorial routes and current ThinkTogether pages stay intact', async () => {
    for (const from of ['/board/board_00000000-0000-0000-0000-000000000001', '/dash/users/settings/users', '/dash/org/settings/landing', '/dash/developers/api', '/thinktogether/group', '/thinktogether/billing', '/thinktogether/expert']) {
      expect((await get(from)).headers.get('location')).toBeNull()
    }
  })
  test('ordinary LearnHouse routes retain their existing routing when the module is disabled', async () => {
    process.env.NEXT_PUBLIC_THINKTOGETHER = 'false'
    for (const from of ['/paths', '/trail', '/boards', '/live', '/store', '/account/purchases', '/new', '/organizations']) {
      expect((await get(from)).headers.get('location')).toBeNull()
    }
  })
})
