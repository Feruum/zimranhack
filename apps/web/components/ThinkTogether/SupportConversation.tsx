'use client'
import { Badge } from '@components/ui/badge'
import { Textarea } from '@components/ui/textarea'
import { Button } from '@components/ui/button'

import { useRef, useState } from 'react'
import { ttApi, ttPost } from '@services/thinktogether/api'
import { formatDate, humanize } from '@services/thinktogether/helpers'
import type { SupportStatus, SupportTicket } from '@services/thinktogether/support'
import { styles } from './Shell'
import { ErrorNotice, Field, Status } from './Shared'

export default function SupportConversation({ ticket, staff = false, onSaved }: { ticket: SupportTicket; staff?: boolean; onSaved: (_ticket: SupportTicket) => void }) {
  const [message, setMessage] = useState('')
  const [status, setStatus] = useState<SupportStatus>(ticket.status)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [saved, setSaved] = useState(false)
  const submissions = useRef(new Map<string, string>())
  async function submit() {
    setBusy(true); setError(null); setSaved(false)
    try {
      const text = message.trim()
      if (!staff && !text) throw new Error('Enter a reply before sending.')
      const payload = staff ? { status, ...(text ? { message: text } : {}) } : { message: text }
      const fingerprint = JSON.stringify(payload)
      let key = submissions.current.get(fingerprint)
      if (!key) { key = crypto.randomUUID(); submissions.current.set(fingerprint, key) }
      const path = staff ? `staff/support/${ticket.id}` : `support/tickets/${ticket.id}/messages`
      const body = { ...payload, idempotency_key: key }
      const updated = staff ? await ttApi<SupportTicket>(path, { method: 'PATCH', body: JSON.stringify(body) }) : await ttPost<SupportTicket>(path, body)
      onSaved(updated); setMessage(''); setStatus(updated.status); setSaved(true); submissions.current.delete(fingerprint)
    } catch (e) { setError(e as Error) } finally { setBusy(false) }
  }
  return <section className={styles.panel} aria-label={`Support conversation: ${ticket.title}`}>
    <div className={styles.panelHeader}><div><p className="text-sm text-muted-foreground">{humanize(ticket.kind)} support</p><h3>{ticket.title}</h3></div><Badge variant="secondary" >{humanize(ticket.status)}</Badge></div>
    <p className={styles.muted}>Opened {formatDate(ticket.created_at)}</p>
    <div className={styles.chatLog} role="log" aria-label="Support messages">{ticket.messages.length ? ticket.messages.map(entry => <article key={entry.id} className={entry.author === 'staff' ? styles.agentMessage : styles.chatMessage}><strong>{entry.author === 'staff' ? 'Support team' : entry.author === 'system' ? 'Learning workspace' : staff ? 'Learner' : 'You'}</strong><p>{entry.message}</p><small className={styles.muted}>{formatDate(entry.created_at)}</small></article>) : <p className={styles.muted}>No messages have been added to this request.</p>}</div>
    {staff && ticket.kind === 'item_shortage' && ticket.details && <details className={styles.details}><summary>Case availability details</summary><pre>{JSON.stringify(ticket.details, null, 2)}</pre></details>}
    <form className={styles.form} onSubmit={e => { e.preventDefault(); submit() }}>
      {staff && <Field label="Request status"><select className="h-10 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50" value={status} onChange={e => setStatus(e.target.value as SupportStatus)} disabled={busy}><option value="open">Open</option><option value="in_progress">In progress</option><option value="resolved">Resolved</option></select></Field>}
      <Field label={staff ? 'Reply to learner (optional)' : 'Your reply'}><Textarea value={message} onChange={e => { setMessage(e.target.value); setSaved(false) }} required={!staff} maxLength={6000} rows={4} disabled={busy} /></Field>
      <ErrorNotice error={error} />{saved && <Status>{staff ? 'Support request updated.' : 'Reply sent.'}</Status>}
      <div className={styles.actions}><Button  disabled={busy || (staff ? status === ticket.status && !message.trim() : !message.trim())}>{busy ? 'Saving…' : staff ? 'Save support update' : 'Send reply'}</Button></div>
    </form>
  </section>
}
