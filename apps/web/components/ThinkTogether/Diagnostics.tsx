'use client'
import { Badge } from '@components/ui/badge'
import { Button } from '@components/ui/button'
import { Label } from '@components/ui/label'

import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ttApi, ttPost, useTTQuery } from '@services/thinktogether/api'
import type { Diagnostic, DiagnosticOptions, Attempt, Delivery } from '@services/thinktogether/types'
import { humanize } from '@services/thinktogether/helpers'
import { styles } from './Shell'
import { ErrorNotice, Loading, Markdown, Status } from './Shared'
import TaskRenderer from './TaskRenderer'
import AttemptResult from './AttemptResult'

export default function Diagnostics({ profileReady }: { profileReady: boolean }) {
  const options = useTTQuery<DiagnosticOptions>('me/diagnostics')
  const [active, setActive] = useState<Diagnostic | null>(null)
  const [result, setResult] = useState<Attempt | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [accepted, setAccepted] = useState<Record<string, boolean>>({})
  const client = useQueryClient()
  useEffect(() => {
    const run = new URLSearchParams(window.location.search).get('diagnostic')
    if (run) ttApi<Diagnostic>(`me/diagnostics/${encodeURIComponent(run)}`).then(setActive).catch(setError)
  }, [])
  async function runAction(action: () => Promise<Diagnostic>) {
    setBusy(true); setError(null); setResult(null)
    try { const response = await action(); setActive(response); const url = new URL(window.location.href); url.searchParams.set('diagnostic', response.run.id); window.history.replaceState(null, '', url); await client.invalidateQueries({ queryKey: ['thinktogether'] }) } catch (e) { setError(e as Error) } finally { setBusy(false) }
  }
  return <section className={styles.assessmentSection} id="assessments"><div className={styles.panelHeader}><div><h2>Assessments</h2></div><Badge variant="secondary" >Domain + cognitive</Badge></div><p className={styles.muted}>Read the consent for each expert-approved assessment before starting. Criterion scores describe this assessment; they do not imply intelligence labels, population norms, or a clinical diagnosis.</p><ErrorNotice error={error || options.error} />
    {!profileReady && <p className={styles.notice}>Save your complete learning profile to begin.</p>}
    {active ? <div className={styles.panel}><div className={styles.panelHeader}><h3>{humanize(active.run.kind)} assessment</h3><span>{active.answered} / {active.total} answered</span></div>
      {result ? <>{active.run.kind === 'cognitive' ? <Status>Response saved. Continue assessment.</Status> : <AttemptResult attempt={result} />}<Button  disabled={busy} onClick={() => runAction(() => ttApi<Diagnostic>(`me/diagnostics/${active.run.id}`))}>Continue assessment</Button></> : active.item && active.delivery_id ? <TaskRenderer key={active.delivery_id} item={active.item} submit={async response => {
        const delivery = active as Diagnostic & Delivery
        const key = `tt-diagnostic-${delivery.delivery_id}`
        let idempotency = sessionStorage.getItem(key); if (!idempotency) { idempotency = crypto.randomUUID(); sessionStorage.setItem(key, idempotency) }
        const attempt = await ttPost<Attempt>('attempts', { item_id: delivery.item.id, delivery_id: delivery.delivery_id, response, idempotency_key: idempotency })
        setResult(attempt); await client.invalidateQueries({ queryKey: ['thinktogether'] })
      }} /> : <><h3>{active.run.status === 'completed' ? 'Assessment completed' : 'Your responses are awaiting review'}</h3><p className={styles.muted}>{active.run.status === 'completed' ? 'Your private criterion results are reflected in your profile.' : 'Your assessment will be complete when all responses have been evaluated.'}</p>{active.run.result?.dimension_scores && <dl className={styles.resultDimensions}>{Object.entries(active.run.result.dimension_scores).map(([name, score]) => <div key={name}><dt>{humanize(name)}</dt><dd>{Math.round(score * 100)}%</dd></div>)}</dl>}<Button variant="outline"  onClick={() => runAction(() => ttApi<Diagnostic>(`me/diagnostics/${active.run.id}`))}>Refresh assessment</Button></>}
      <Button variant="link" size="sm" className="h-auto px-0 text-xs"  onClick={() => { setActive(null); setResult(null); const url = new URL(window.location.href); url.searchParams.delete('diagnostic'); window.history.replaceState(null, '', url) }}>Back to assessments</Button>
    </div> : options.isPending ? <Loading /> : <div className={styles.equalGrid}>{(['domain', 'cognitive'] as const).map(kind => <div className={styles.panel} key={kind}><h3>{kind === 'domain' ? 'Domain diagnostic' : 'Cognitive assessment'}</h3>{options.data?.runs.filter(run => run.kind === kind).map(run => <div className={styles.row} key={run.id}><span>{humanize(run.status)}</span><Button variant="outline"  disabled={busy} onClick={() => runAction(() => ttApi<Diagnostic>(`me/diagnostics/${run.id}`))}>{run.status === 'in_progress' ? 'Resume' : 'View'}</Button></div>)}{!options.data?.instruments.some(i => i.kind === kind) && <p className={styles.muted}>No approved {kind === 'domain' ? 'domain diagnostic' : 'cognitive assessment'} available yet.</p>}{options.data?.instruments.filter(i => i.kind === kind).map(instrument => <div key={instrument.id}><h4>{instrument.title}</h4><details className={styles.consent}><summary>Read assessment consent · version {instrument.consent_version}</summary><Markdown>{instrument.consent_text}</Markdown></details><Label className={styles.choice}><input className="mt-0.5 size-4 shrink-0 accent-primary" type="checkbox" checked={accepted[instrument.id] ?? instrument.consented} onChange={e => setAccepted({ ...accepted, [instrument.id]: e.target.checked })} /><span>I have read the consent and agree to this assessment.</span></Label><Button  disabled={!profileReady || busy || !(accepted[instrument.id] ?? instrument.consented)} onClick={() => runAction(async () => { await ttPost('me/consents', { instrument_id: instrument.id, version: instrument.consent_version, accepted: true }); return ttPost<Diagnostic>('me/diagnostics', { instrument_id: instrument.id, kind }) })}>{busy ? 'Starting…' : 'Start assessment'}</Button>{instrument.consented && <Button variant="link" size="sm" className="h-auto px-0 text-xs"  disabled={busy} onClick={async () => { try { await ttPost('me/consents', { instrument_id: instrument.id, version: instrument.consent_version, accepted: false }); setAccepted({ ...accepted, [instrument.id]: false }); await options.refetch() } catch (e) { setError(e as Error) } }}>Withdraw consent</Button>}</div>)}</div>)}</div>}
  </section>
}
