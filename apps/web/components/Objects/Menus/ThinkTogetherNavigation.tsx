'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useTranslation } from 'react-i18next'
import { BookOpen, ChartLineUp, Compass, CreditCard, DotsNine, GraduationCap, Stack, UserCircle, UsersThree } from '@phosphor-icons/react'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { getConfig, getUriWithOrg } from '@services/config/config'
import { useTTQuery, type TTBootstrap } from '@services/thinktogether/api'
import { coreNavigation } from '@services/thinktogether/navigation'
import { DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger } from '@components/ui/dropdown-menu'

const icons: Record<string, typeof Compass> = {
  'tt-today': Compass, 'tt-program': BookOpen, 'tt-practice': Stack, 'tt-group': UsersThree,
  'tt-progress': ChartLineUp, 'tt-profile': UserCircle, 'tt-billing': CreditCard, 'tt-expert': GraduationCap,
}

export function useCoreMenuItems(orgslug: string) {
  const session = useLHSession() as { status?: string }
  const enabled = getConfig('NEXT_PUBLIC_THINKTOGETHER') === 'true'
  const authenticated = session?.status === 'authenticated'
  const bootstrap = useTTQuery<TTBootstrap>('bootstrap', enabled && authenticated)
  return coreNavigation(enabled, authenticated && bootstrap.data?.is_staff === true).map(item => ({ ...item, href: getUriWithOrg(orgslug, item.path), Icon: icons[item.key] }))
}

export function CoreDesktopMore({ orgslug, className }: { orgslug: string; className?: string }) {
  const { t } = useTranslation()
  const items = useCoreMenuItems(orgslug)
  const path = (usePathname() || '/').replace(/^\/orgs\/[^/]+/, '')
  if (!items.length) return null
  return <DropdownMenu>
    <DropdownMenuTrigger asChild><button type="button" className={`flex items-center gap-2 rounded-md py-1 font-semibold ${className || ''}`} aria-label={t('common.more')} data-active={path.startsWith('/thinktogether/') || undefined}><DotsNine size={20} weight="fill" aria-hidden /><span className="sr-only xl:not-sr-only">{t('common.more')}</span></button></DropdownMenuTrigger>
    <DropdownMenuContent align="start" className="w-56">
      <DropdownMenuLabel>Core</DropdownMenuLabel>
      <DropdownMenuGroup aria-label="Core">{items.map(item => <DropdownMenuItem key={item.key} asChild><Link href={item.href} aria-current={path === item.path || path.startsWith(`${item.path}/`) ? 'page' : undefined}><item.Icon size={16} aria-hidden /><span>{item.label}</span></Link></DropdownMenuItem>)}</DropdownMenuGroup>
    </DropdownMenuContent>
  </DropdownMenu>
}
