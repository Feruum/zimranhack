import { beforeEach, describe, expect, mock, test } from 'bun:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

let enabled = 'true'
let status = 'authenticated'
mock.module('@services/config/config', () => ({ getConfig: () => enabled }))
mock.module('@components/Contexts/OrgContext', () => ({ useOrg: () => ({ config: { config: {} } }) }))
mock.module('@components/Contexts/LHSessionContext', () => ({ useLHSession: () => ({ status }) }))
mock.module('@services/plans/plans', () => ({ isFeatureAvailable: () => true }))
const { useSitePages } = await import('../lib/site-search/useSitePages')
const { searchMetas: commercePages } = await import('../app/orgs/[orgslug]/dash/payments/page.search')
const { searchMetas: accountPages } = await import('../app/orgs/[orgslug]/(withmenu)/account/page.search')

function SearchEntries() {
  return React.createElement('div', null, useSitePages().map(page => React.createElement('a', { key: page.id, href: page.href }, page.titleKey)))
}
const entries = () => renderToStaticMarkup(React.createElement(SearchEntries))
beforeEach(() => { enabled = 'true'; status = 'authenticated' })
describe('B2C navigation in learner search and keyboard shortcuts', () => {
  test('does not offer removed LearnHouse catalogs or duplicate payment pages', () => {
    const html = entries()
    for (const path of ['/paths', '/podcasts', '/communities', '/live', '/boards', '/store', '/account/purchases']) {
      expect(html).not.toContain(`href="${path}"`)
    }
    expect(html).toContain('href="/courses"')
    expect(html).toContain('href="/library"')
  })
  test('Progress and its existing shortcut open ThinkTogether mastery', () => {
    expect(entries()).toContain('href="/thinktogether/progress"')
    expect(entries()).not.toContain('href="/trail"')
  })
  test('does not offer private progress after signing out', () => {
    status = 'unauthenticated'
    expect(entries()).not.toContain('/thinktogether/progress')
  })
  test('ordinary LearnHouse search retains its existing destinations', () => {
    enabled = 'false'
    for (const path of ['/paths', '/podcasts', '/communities', '/boards', '/store', '/trail', '/account/purchases']) {
      expect(entries()).toContain(`href="${path}"`)
    }
  })
  test('staff search also suppresses duplicate Stripe and purchase pages', () => {
    for (const page of [...commercePages, accountPages.find(page => page.id === 'account.purchases')]) {
      expect(page.available?.()).toBe(false)
    }
    enabled = 'false'
    for (const page of commercePages) expect(page.available?.()).toBe(true)
  })
})
