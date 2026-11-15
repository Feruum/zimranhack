const CORE_SECTIONS = [
  ['today', 'Today'],
  ['program', 'My program'],
  ['practice', 'Practice'],
  ['group', 'My group'],
  ['progress', 'My progress'],
  ['profile', 'My profile'],
  ['billing', 'My access'],
] as const

export function coreNavigation(enabled: boolean, isStaff: boolean) {
  if (!enabled) return []
  const items: { key: string; label: string; path: string }[] = CORE_SECTIONS.map(([slug, label]) => ({ key: `tt-${slug}`, label, path: `/thinktogether/${slug}` }))
  if (isStaff) items.push({ key: 'tt-expert', label: 'Expert workspace', path: '/thinktogether/expert' })
  return items
}

export function isCoreDestination(href: string) {
  try {
    const path = new URL(href, 'https://navigation.local').pathname.replace(/^\/orgs\/[^/]+/, '')
    return path === '/thinktogether' || path.startsWith('/thinktogether/')
  } catch { return false }
}

/** Canonical B2C page entry points. Board documents and authoring stay native. */
export function thinkTogetherDestination(href: string): string | null {
  if (!href.startsWith('/') || href.startsWith('//')) return null
  const url = new URL(href, 'https://navigation.local')
  const prefix = url.pathname.match(/^\/orgs\/[^/]+(?=\/|$)/)?.[0] || ''
  const path = url.pathname.slice(prefix.length).replace(/\/+$/, '') || '/'
  if (path === '/home') return `${prefix}/${url.search}${url.hash}`
  const routes: [string[], string][] = [
    [['/paths', '/path', '/podcasts'], '/thinktogether/program'],
    [['/trail'], '/thinktogether/progress'],
    [['/communities', '/live', '/boards'], '/thinktogether/group'],
    [['/store', '/billing', '/subscriptions', '/account/purchases', '/payments/stripe/connect/oauth'], '/thinktogether/billing'],
    [['/new', '/organizations'], '/thinktogether/today'],
    [['/dash/payments'], '/thinktogether/expert'],
  ]
  const target = routes.find(([sources]) => sources.some(source => path === source || path.startsWith(`${source}/`)))?.[1]
  return target ? `${prefix}${target}${url.search}${url.hash}` : null
}
