'use client'
import { SkiperLink } from '@components/ui/skiper-ui/css-link'
import { Button } from '@components/ui/button'

import { useTTQuery, ttPost } from '@services/thinktogether/api'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { styles } from './Shell'
import { ErrorNotice } from './Shared'

export default function Notifications() {
  const query = useTTQuery<{ id: number; title: string; href: string; read: boolean }[]>('notifications')
  const client = useQueryClient()
  const [error, setError] = useState<Error | null>(null)
  const unread = query.data?.filter(n => !n.read) || []
  if (query.isPending || (!query.error && !unread.length)) return null
  return <section className={styles.panel}><h2>Updates for you</h2><ErrorNotice error={query.error || error} /><ul className={styles.list}>{unread.slice(0, 5).map(n => <li key={n.id}><SkiperLink className="text-sm font-medium"  href={n.href.startsWith('/thinktogether/') ? n.href : '/thinktogether/today'}>{n.title}</SkiperLink><Button variant="link" size="sm" className="h-auto px-0 text-xs"  onClick={async () => { try { await ttPost(`notifications/${n.id}/read`); await client.invalidateQueries({ queryKey: ['thinktogether', 'notifications'] }) } catch (e) { setError(e as Error) } }}>Mark read</Button></li>)}</ul></section>
}
