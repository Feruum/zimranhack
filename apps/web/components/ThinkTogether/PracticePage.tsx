'use client'
import { Badge } from '@components/ui/badge'
import { Button } from '@components/ui/button'
import { SkiperLink } from '@components/ui/skiper-ui/css-link'

import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ttApi, ttPost, useTTQuery } from '@services/thinktogether/api'
import type { Attempt, Delivery, PracticeMode, Skill } from '@services/thinktogether/types'
import { attemptSummary } from '@services/thinktogether/helpers'
import { EmptyState, PageHeading, styles } from './Shell'
import { ErrorNotice, Field, Loading } from './Shared'
import TaskRenderer from './TaskRenderer'
import AttemptResult from './AttemptResult'
import { practiceRequest } from './PracticeMechanics'

export default function PracticePage({ initialSkill = '', initialReview = '' }: { initialSkill?: string; initialReview?: string }) {
  const skills = useTTQuery<Skill[]>('skills')
  const [delivery, setDelivery] = useState<Delivery | null>(null)
  const [result, setResult] = useState<Attempt | null>(null)
  const [selected, setSelected] = useState(initialSkill)
  const [mode, setMode] = useState<PracticeMode>('case')
  const [review, setReview] = useState(initialReview)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [hint, setHint] = useState('')
  const [hintIndex, setHintIndex] = useState(0)
  const [started, setStarted] = useState(0)
  const client = useQueryClient()
  async function next(nextMode: PracticeMode = mode, transfer = false) {
    setBusy(true); setError(null); setHint(''); setHintIndex(0); setResult(null)
    try {
      const issued = await ttApi<Delivery>(practiceRequest(nextMode, selected, review, transfer))
      setDelivery(issued); setStarted(Date.now())
    } catch (e) { setDelivery(null); setError(e as Error) } finally { setBusy(false) }
  }
  return <><PageHeading  title="Practice"  /><div className={styles.grid}><section className={`${styles.panel} space-y-4`}>
    <ErrorNotice error={error} />{!delivery ? <><Field label="Choose a skill"><select className="h-10 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50" disabled={busy} value={selected} onChange={e => { setSelected(e.target.value); setReview('') }}><option value="">Use my plan</option>{skills.data?.map(s => <option key={s.id} value={s.id}>{s.title}</option>)}</select></Field>{!review && <Field label="Practice format"><select className="h-10 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50" disabled={busy} value={mode} onChange={e => setMode(e.target.value as PracticeMode)}><option value="case">QuestPath · case practice</option><option value="retell">Retell · explain from memory</option><option value="think_first">ThinkFirst · critique an AI answer</option></select></Field>}{review && <Badge variant="secondary" >Due retrieval review</Badge>}<div className={styles.actions}><Button  onClick={() => next()} disabled={busy}>{busy ? 'Loading…' : review ? 'Start review' : 'Start practice'}</Button></div><ErrorNotice error={skills.error} />{!skills.isPending && !skills.data?.length && <p className={styles.muted}>Choose a program in your profile to see its skills.</p>}<div className={styles.actions}><SkiperLink className="text-sm font-medium"  href="/thinktogether/profile">Review my profile and assessments</SkiperLink><SkiperLink className="text-sm font-medium"  href="/thinktogether/billing">Check my access</SkiperLink></div></> : result ? <><AttemptResult attempt={result} />{delivery.phase === 'coached' && <p className={styles.notice}>This coached response supports learning and does not establish mastery. Now apply the principle to a different case independently.</p>}<div className={styles.actions}><Button  disabled={busy} onClick={() => { setReview(''); setSelected(''); setMode('case'); next('case', true) }}>{busy ? 'Finding a fresh case…' : delivery.phase === 'coached' ? 'Apply it to a fresh case' : result.score !== null && result.score < .8 ? 'Try a fresh case on this principle' : 'Continue with a fresh case'}</Button><Button variant="outline"  disabled={busy} onClick={() => { setReview(''); setDelivery(null); setResult(null) }}>Choose another format</Button></div></> : <><p className="text-sm text-muted-foreground">{delivery.phase === 'coached' ? delivery.item.practice_mode === 'retell' ? 'Retell · coached practice' : 'ThinkFirst · coached practice' : delivery.phase === 'preview' ? 'Staff sample preview' : review ? 'Retrieval review' : 'Independent practice'}</p>{delivery.selection_reason === 'same_principle_after_error' && <p className={styles.notice}>A new situation, the same principle. Use the feedback from your previous response.</p>}{delivery.selection_reason === 'same_principle_after_coached_practice' && <p className={styles.notice}>The study example is complete. Apply its principle to this fresh case independently.</p>}<TaskRenderer key={delivery.delivery_id} item={delivery.item} pending={busy} submit={async response => {
      const key = `tt-attempt-${delivery.delivery_id}`; let idempotency = sessionStorage.getItem(key)
      if (!idempotency) { idempotency = crypto.randomUUID(); sessionStorage.setItem(key, idempotency) }
      setResult(await ttPost<Attempt>('attempts', { item_id: delivery.item.id, delivery_id: delivery.delivery_id, response, idempotency_key: idempotency, elapsed_seconds: Math.min(86400, Math.max(0, Math.round((Date.now() - started) / 1000))) }))
      await client.invalidateQueries({ queryKey: ['thinktogether'] })
    }} />{hint && <aside className={styles.notice}><h3>Hint</h3><p>{hint}</p><small>Using a hint makes this an assisted attempt.</small></aside>}{delivery.phase !== 'coached' && <Button variant="link" size="sm" className="h-auto px-0 text-xs"  disabled={busy} onClick={async () => { setBusy(true); setError(null); try { const data = await ttPost<{ hint: string }>(`practice/${delivery.delivery_id}/hint?hint_index=${hintIndex}`); setHint(data.hint); setHintIndex(hintIndex + 1) } catch (e) { setError(e as Error) } finally { setBusy(false) } }}>Request a hint</Button>}</>}
  </section><aside className={styles.stack}><RecentAttempts /></aside></div></>
}

export function RecentAttempts() {
  const attempts = useTTQuery<(Pick<Attempt, 'id' | 'phase' | 'assessment_kind' | 'created_at' | 'assisted' | 'score' | 'status'> & { title: string })[]>('me/attempts')
  const [selected, setSelected] = useState('')
  const detail = useTTQuery<Attempt>(`attempts/${selected}`, !!selected)
  return <section className={`${styles.panel} space-y-4`}><h2>Recent responses</h2>{attempts.isPending ? <Loading /> : attempts.error ? <ErrorNotice error={attempts.error} retry={() => attempts.refetch()} /> : !attempts.data?.length ? <p className={styles.muted}>No responses yet.</p> : <ul className={styles.list}>{attempts.data.slice(0, 8).map(attempt => <li key={attempt.id}><Button variant="link" className="h-auto justify-start whitespace-normal p-0 text-start"  onClick={() => setSelected(attempt.id)}>{attempt.title}</Button><p>{attemptSummary(attempt)}{attempt.assisted ? ' · assisted' : ''}</p></li>)}</ul>}{selected && (detail.isPending ? <Loading /> : detail.error ? <ErrorNotice error={detail.error} /> : detail.data ? <><AttemptResult attempt={detail.data} /><Button variant="link" size="sm" className="h-auto px-0 text-xs"  onClick={() => setSelected('')}>Close response</Button></> : <EmptyState title="Response unavailable"><p>Please select another saved response.</p></EmptyState>)}</section>
}
