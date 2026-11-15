import { beforeEach, describe, expect, mock, test } from 'bun:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { readFileSync } from 'node:fs'
import { coreNavigation } from '../services/thinktogether/navigation'

let enabled = 'true'
let organization
let sessionStatus = 'authenticated'
let staff = false
let admin = false
mock.module('@components/Contexts/OrgContext', () => ({ useOrg: () => organization }))
mock.module('@components/Contexts/LHSessionContext', () => ({ useLHSession: () => ({ status: sessionStatus, data: { user: { username: 'learner' } } }) }))
mock.module('@components/Contexts/AuthContext', () => ({ signOut: () => undefined }))
mock.module('@services/thinktogether/api', () => ({ useTTQuery: () => ({ data: { is_staff: staff } }) }))
mock.module('@services/config/config', () => ({
  getConfig: key => key === 'NEXT_PUBLIC_THINKTOGETHER' ? enabled : undefined,
  getUriWithOrg: (slug, path) => `/orgs/${slug}${path}`,
  getMainDomainUri: path => `https://platform.test${path}`,
  isMultiOrgModeEnabled: () => true,
}))
mock.module('react-i18next', () => ({ useTranslation: () => ({ t: (key, options) => ({ 'common.more': 'More', 'common.home': 'Home' }[key] || options?.defaultValue || key), i18n: { language: 'en' } }) }))
mock.module('next/navigation', () => ({ usePathname: () => '/thinktogether/today' }))
mock.module('next/link', () => ({ default: ({ children, href, ...props }) => React.createElement('a', { href, ...props }, children) }))
mock.module('@components/Objects/Shortcuts/ShortcutHint', () => ({ ShortcutHint: ({ children }) => children }))
mock.module('@/lib/site-search/registry', () => ({ sitePages: [] }))
mock.module('@components/Objects/UserAvatar', () => ({ default: () => null }))
mock.module('@components/Objects/Org/OrgSquareLogo', () => ({ default: () => null }))
mock.module('@components/Objects/StyledElements/Tooltip/Tooltip', () => ({ default: ({ children }) => children }))
mock.module('@components/Hooks/useAdminStatus', () => ({ default: () => ({ rights: admin ? { dashboard: { action_access: true } } : {}, userRoles: [], canManageOrg: admin }), seesPlatformHubFromSession: () => admin }))
mock.module('@components/Messaging/MessagingProvider', () => ({ useMessagingEnabled: () => false, useMessaging: () => ({ unreadTotal: 0 }) }))
mock.module('@components/Objects/Search/SiteCommandPaletteContext', () => ({ useSiteCommandPalette: () => ({ setOpen: () => undefined }) }))
mock.module('@services/plans/plans', () => ({ isFeatureAvailable: () => true }))
mock.module('@/lib/i18n', () => ({ changeLanguage: () => undefined }))
mock.module('@ee/components/Analytics/availability', () => ({ isOrgAnalyticsAvailable: () => false }))
mock.module('@components/Utils/LanguageSwitcher', () => ({ default: () => null }))
mock.module('@services/analytics', () => ({ useLHAnalytics: () => ({ track: () => undefined }), AnalyticsEvent: {} }))

// Render native menu children without Radix's portal lifecycle. Production
// still uses DropdownMenu and the existing mobile Dialog for focus and input.
const childrenOnly = ({ children }) => React.createElement(React.Fragment, null, children)
mock.module('@components/ui/dropdown-menu', () => ({
  DropdownMenu: childrenOnly, DropdownMenuTrigger: childrenOnly, DropdownMenuItem: childrenOnly,
  DropdownMenuGroup: childrenOnly, DropdownMenuLabel: ({ children }) => React.createElement('h3', null, children),
  DropdownMenuContent: ({ children }) => React.createElement('div', { role: 'menu' }, children),
  DropdownMenuSeparator: () => null, DropdownMenuSub: childrenOnly, DropdownMenuSubTrigger: childrenOnly,
  DropdownMenuSubContent: childrenOnly, DropdownMenuPortal: childrenOnly,
}))
mock.module('@components/ui/dialog', () => ({ Dialog: childrenOnly, DialogContent: childrenOnly, DialogTitle: ({ children }) => React.createElement('h2', null, children) }))

const { default: MenuLinks, useOrgMenuItems } = await import('../components/Objects/Menus/OrgMenuLinks')
const { CoreDesktopMore } = await import('../components/Objects/Menus/ThinkTogetherNavigation')
const { MobileMoreSheet, MobileTabBar } = await import('../components/Objects/Menus/MobileNav')
const { HeaderProfileBox } = await import('../components/Security/HeaderProfileBox')
const { AccountSidebar } = await import('../components/Objects/Account/AccountSidebar')
const { AccountActionsMobile } = await import('../components/Objects/Account/AccountActionsMobile')
const expectedSections = ['today', 'program', 'practice', 'group', 'progress', 'profile', 'billing']
function desktop() { return renderToStaticMarkup(React.createElement(CoreDesktopMore, { orgslug: 'school' })) }
function mobile() { return renderToStaticMarkup(React.createElement(MobileMoreSheet, { open: true, onOpenChange: () => undefined, orgslug: 'school' })) }
function bottomBar() { return renderToStaticMarkup(React.createElement(MobileTabBar, { orgslug: 'school' })).match(/<nav\b[^>]*>[\s\S]*?<\/nav>/)?.[0] || '' }
function profileMenu() { return renderToStaticMarkup(React.createElement(HeaderProfileBox)) }

beforeEach(() => {
  enabled = 'true'; staff = false; admin = false; sessionStatus = 'authenticated'
  organization = { config: { config: { resolved_features: { courses: { enabled: true }, paths: { enabled: true }, folders: { enabled: true } } } } }
})

describe('Core in native LearnHouse More menus', () => {
  test('offers a visible Home link outside the desktop More menu', () => {
    const html = renderToStaticMarkup(React.createElement(MenuLinks, { orgslug: 'school' }))
    expect(html).toMatch(/<a[^>]*href="\/orgs\/school\/"[^>]*>[\s\S]*?<span>Home<\/span>/)
    expect(bottomBar()).toContain('href="/orgs/school/"')
  })
  test('opens ThinkTogether directly from the bottom bar while retaining native destinations', () => {
    const html = bottomBar()
    expect(html).toContain('href="/orgs/school/thinktogether/today"')
    expect(html).toContain('aria-label="ThinkTogether — Today"')
    expect(html).toMatch(/<a[^>]*href="\/orgs\/school\/thinktogether\/today"[^>]*aria-current="page"/)
    expect(html).toContain('href="/orgs/school/courses"')
    expect(html).toContain('href="/orgs/school/thinktogether/progress"')
    expect(html).not.toContain('href="/orgs/school/trail"')
    expect(html).toContain('aria-label="More"')
  })
  test('does not add ThinkTogether to the bottom bar when the module is disabled', () => {
    enabled = 'false'
    expect(bottomBar()).not.toContain('/thinktogether/')
  })
  test('keeps Courses and Library while the personal program owns learning paths', () => {
    const links = useOrgMenuItems('school')
    expect(links.map(item => item.key)).toEqual(['courses', 'library'])
    expect(links.find(item => item.key === 'courses')?.href).toBe('/orgs/school/courses')
  })
  test('desktop More and mobile More contain all seven Core destinations', () => {
    for (const html of [desktop(), mobile()]) {
      expect(html).toContain('Core</h3>')
      for (const section of expectedSections) expect(html).toContain(`href="/orgs/school/thinktogether/${section}"`)
      expect(html).not.toContain('/thinktogether/expert')
    }
    expect(desktop()).toContain('aria-label="More"')
  })
  test('shows Expert workspace to staff in both More surfaces', () => {
    staff = true
    expect(desktop()).toContain('href="/orgs/school/thinktogether/expert"')
    expect(mobile()).toContain('href="/orgs/school/thinktogether/expert"')
  })
  test('does not expose cached staff navigation after sign out', () => {
    staff = true; sessionStatus = 'unauthenticated'
    expect(desktop()).not.toContain('/thinktogether/expert')
    expect(mobile()).not.toContain('/thinktogether/expert')
  })
  test('ordinary LearnHouse installations keep Core disabled', () => {
    enabled = 'false'
    expect(coreNavigation(false, true)).toEqual([])
    expect(desktop()).toBe('')
    expect(mobile()).not.toContain('/thinktogether/')
    expect(useOrgMenuItems('school').map(item => item.key)).toEqual(['courses', 'paths', 'library'])
  })
  test('moves custom local Core links into More while retaining unrelated custom links', () => {
    organization.config.config.customization = { menu: { items: [
      { type: 'custom', enabled: true, label: 'Help', url: 'https://help.example.test', order: 0 },
      { type: 'custom', enabled: true, label: 'My learning', url: '/thinktogether/today', order: 1 },
    ] } }
    expect(useOrgMenuItems('school')).toHaveLength(1)
    expect(useOrgMenuItems('school')[0]).toMatchObject({ label: 'Help', external: true, href: 'https://help.example.test' })
  })
  test('removes legacy community, media, path and store destinations from learner menus', () => {
    for (const feature of ['podcasts', 'live_sessions', 'communities', 'payments', 'boards', 'playgrounds']) {
      organization.config.config.resolved_features[feature] = { enabled: true }
    }
    expect(useOrgMenuItems('school').map(item => item.key)).toEqual(['courses', 'library', 'playgrounds'])
    const html = mobile()
    for (const path of ['/podcasts', '/communities', '/paths', '/live', '/boards', '/store', '/account/purchases']) {
      expect(html).not.toContain(`href="/orgs/school${path}"`)
    }
    expect(html).toContain('href="/orgs/school/thinktogether/group"')
    expect(html).toContain('href="/orgs/school/thinktogether/billing"')
  })
  test('hides paused administration modules while retaining the expert workspace', () => {
    admin = true; staff = true
    const html = mobile()
    for (const path of ['/dash/users/settings/users', '/dash/org/settings/general', '/dash/developers/api']) {
      expect(html).not.toContain(`href="${path}"`)
    }
    expect(html).not.toContain('/dash/payments/')
    expect(html).toContain('/thinktogether/expert')
  })
  test('ordinary LearnHouse retains its administration modules', () => {
    admin = true; staff = true; enabled = 'false'
    const html = mobile()
    for (const path of ['/dash/users/settings/users', '/dash/org/settings/general', '/dash/developers/api']) {
      expect(html).toContain(`href="${path}"`)
    }
  })
  test('ordinary learners cannot see dashboard or developer entries', () => {
    expect(mobile()).not.toContain('href="/dash')
  })
  test('ThinkTogether profiles hide organization creation, switching and duplicate purchases', () => {
    admin = true
    const html = profileMenu()
    expect(html).not.toContain('platform.test/new')
    expect(html).not.toContain('platform.test/home')
    expect(html).not.toContain('platform.test/billing')
    expect(html).not.toContain('/account/purchases')
  })
  test('ordinary LearnHouse profiles retain their platform controls', () => {
    admin = true; enabled = 'false'
    const html = profileMenu()
    expect(html).toContain('platform.test/new')
    expect(html).toContain('platform.test/billing')
    expect(html).toContain('/account/purchases')
  })
  test('account navigation uses Core access instead of a second purchases section', () => {
    for (const Component of [AccountSidebar, AccountActionsMobile]) {
      const html = renderToStaticMarkup(React.createElement(Component, { orgslug: 'school', currentSubpage: 'general' }))
      expect(html).not.toContain('/account/purchases')
      expect(html).toContain('/account/security')
    }
  })
  test('ordinary LearnHouse account navigation retains purchases', () => {
    enabled = 'false'
    for (const Component of [AccountSidebar, AccountActionsMobile]) {
      expect(renderToStaticMarkup(React.createElement(Component, { orgslug: 'school', currentSubpage: 'general' }))).toContain('/account/purchases')
    }
  })
  test('removes inline ThinkTogether navigation and the standalone expert shortcut', () => {
    const shell = readFileSync(new URL('../components/ThinkTogether/Shell.tsx', import.meta.url), 'utf8')
    expect(shell).not.toContain('ThinkTogether sections')
    expect(shell).not.toContain('nativeNav')
    expect(shell).not.toContain('/thinktogether/expert')
    expect(shell).toContain('GeneralWrapperStyled')
    expect(shell).toContain('signOut({ callbackUrl: loginHref, redirect: true })')
    expect(shell).toContain('ThinkTogether source code')
  })
})
