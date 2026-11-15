'use client'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import { Textarea } from '@components/ui/textarea'

import { useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ttPost, useTTQuery } from '@services/thinktogether/api'
import { formatDate, humanize } from '@services/thinktogether/helpers'
import { SUPPORT_KINDS, supportRequest, type SupportKind, type SupportTicket } from '@services/thinktogether/support'
import { styles } from './Shell'
import { ErrorNotice, Field, Loading, Status } from './Shared'
import SupportConversation from './SupportConversation'

export default function SupportPanel() {
  const query = useTTQuery<SupportTicket[]>('support/tickets')
  const attempts = useTTQuery<{ id: string; title: string }[]>('me/attempts')
  const client = useQueryClient()
  const [selected, setSelected] = useState('')
  const [values, setValues] = useState({ kind: 'learning' as SupportKind, title: '', message: '', attempt_id: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [created, setCreated] = useState(false)
  const submissions = useRef(new Map<string, string>())
  const ticket = query.data?.find(t => t.id === selected)
  function update(updated: SupportTicket) {
    client.setQueryData<SupportTicket[]>(['thinktogether', 'support/tickets'], previous => [updated, ...(previous || []).filter(t => t.id !== updated.id)])
    client.invalidateQueries({ queryKey: ['thinktogether', 'staff/support'] })
  }
  async function create() {
    setBusy(true); setError(null); setCreated(false)
    try {
      const fingerprint = JSON.stringify(values)
      let key = submissions.current.get(fingerprint)
      if (!key) { key = crypto.randomUUID(); submissions.current.set(fingerprint, key) }
      const createdTicket = await ttPost<SupportTicket>('support/tickets', supportRequest(values, key))
      update(createdTicket); setSelected(createdTicket.id); setCreated(true); setValues({ kind: 'learning', title: '', message: '', attempt_id: '' }); submissions.current.delete(fingerprint)
    } catch (e) { setError(e as Error) } finally { setBusy(false) }
  }
  return <section className={styles.assessmentSection} id="support">
    <div className={styles.panelHeader}><div><h2>Help & support</h2></div><Button variant="outline"  disabled={query.isFetching} onClick={() => query.refetch()}>Refresh requests</Button></div>
    <p className={styles.muted}>Contact support about learning, groups, access or assessments.</p>
    <div className={styles.equalGrid}><div className={styles.stack}><section className={styles.panel}><h3>Ask for help</h3><form className={styles.form} onSubmit={e => { e.preventDefault(); create() }}>
      <Field label="Support topic"><select className="h-10 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50" value={values.kind} onChange={e => setValues({ ...values, kind: e.target.value as SupportKind })} disabled={busy}>{SUPPORT_KINDS.map(kind => <option key={kind} value={kind}>{humanize(kind)}</option>)}</select></Field>
      <Field label="Request title"><Input value={values.title} onChange={e => setValues({ ...values, title: e.target.value })} required maxLength={160} disabled={busy} /></Field>
      <Field label="How can we help?"><Textarea value={values.message} onChange={e => setValues({ ...values, message: e.target.value })} required maxLength={6000} rows={5} disabled={busy} /></Field>
      {!!attempts.data?.length && <Field label="Related response (optional)"><select className="h-10 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50" value={values.attempt_id} onChange={e => setValues({ ...values, attempt_id: e.target.value })} disabled={busy}><option value="">No related response</option>{attempts.data.map(attempt => <option key={attempt.id} value={attempt.id}>{attempt.title}</option>)}</select></Field>}
      <ErrorNotice error={error} />{created && <Status>Support request sent.</Status>}<Button  disabled={busy}>{busy ? 'Sending…' : 'Send support request'}</Button>
    </form></section><section className={styles.panel}><h3>Your support requests</h3>{query.isPending ? <Loading>Loading support requests…</Loading> : query.error ? <ErrorNotice error={query.error} retry={() => query.refetch()} /> : !query.data?.length ? <p className={styles.muted}>No support requests yet.</p> : <ul className={styles.list}>{query.data.map(t => <li key={t.id}><Button variant="link" className="h-auto justify-start whitespace-normal p-0 text-start"  onClick={() => setSelected(t.id)} aria-pressed={selected === t.id}>{t.title}</Button><p>{humanize(t.kind)} · {humanize(t.status)} · {formatDate(t.created_at)}</p></li>)}</ul>}</section></div>
      {ticket ? <SupportConversation key={ticket.id} ticket={ticket} onSaved={update} /> : <section className={styles.panel}><h3>Your support conversation</h3><p className={styles.muted}>Select a request to view the conversation.</p></section>}
    </div>
  </section>
}
