'use client'
import { Button } from '@components/ui/button'

import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useTTQuery } from '@services/thinktogether/api'
import { formatDate, humanize } from '@services/thinktogether/helpers'
import type { SupportTicket } from '@services/thinktogether/support'
import { styles } from '../Shell'
import { ErrorNotice, Field, Loading } from '../Shared'
import SupportConversation from '../SupportConversation'

export default function ExpertSupport() {
  const query = useTTQuery<SupportTicket[]>('staff/support')
  const [selected, setSelected] = useState('')
  const [status, setStatus] = useState('active')
  const client = useQueryClient()
  const tickets = query.data?.filter(ticket => status === 'all' || (status === 'active' ? ticket.status !== 'resolved' : ticket.status === status)) || []
  const ticket = query.data?.find(t => t.id === selected)
  function update(updated: SupportTicket) {
    client.setQueryData<SupportTicket[]>(['thinktogether', 'staff/support'], previous => [updated, ...(previous || []).filter(t => t.id !== updated.id)])
    client.invalidateQueries({ queryKey: ['thinktogether', 'support/tickets'] })
  }
  return <div className={styles.grid}><section className={styles.panel}><div className={styles.panelHeader}><h2>Support queue</h2><Button variant="outline"  disabled={query.isFetching} onClick={() => query.refetch()}>Refresh queue</Button></div><Field label="Show requests"><select className="h-10 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50" value={status} onChange={e => setStatus(e.target.value)}><option value="active">Open and in progress</option><option value="open">Open</option><option value="in_progress">In progress</option><option value="resolved">Resolved</option><option value="all">All requests</option></select></Field>{query.isPending ? <Loading>Loading support queue…</Loading> : query.error ? <ErrorNotice error={query.error} retry={() => query.refetch()} /> : !tickets.length ? <p className={styles.muted}>No support requests match this status.</p> : <ul className={styles.list}>{tickets.map(t => <li key={t.id}><Button variant="link" className="h-auto justify-start whitespace-normal p-0 text-start"  onClick={() => setSelected(t.id)} aria-pressed={selected === t.id}>{t.title}</Button><p>{humanize(t.kind)} · {humanize(t.status)} · {formatDate(t.created_at)}</p></li>)}</ul>}</section>{ticket ? <SupportConversation key={ticket.id} ticket={ticket} staff onSaved={update} /> : <section className={styles.panel}><h2>Follow through with the learner</h2><p className={styles.muted}>Select a request to read its conversation, reply, and update its status.</p></section>}</div>
}
