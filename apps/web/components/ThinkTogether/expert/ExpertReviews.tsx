'use client'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import { Textarea } from '@components/ui/textarea'

import { useState } from 'react'
import { useTTQuery, ttApi, ttPost } from '@services/thinktogether/api'
import type { Attempt, Item } from '@services/thinktogether/types'
import { styles, useWorkspace } from '../Shell'
import { ErrorNotice, Field, Loading, Markdown, Status } from '../Shared'

export default function ExpertReviews() {
  const workspace = useWorkspace()
  type ReviewAttempt = Attempt & { item?: Item & { rubric?: Record<string, unknown> } }
  const queue = useTTQuery<ReviewAttempt[]>('staff/review-queue')
  const [selected, setSelected] = useState<ReviewAttempt | null>(null)
  const item = useTTQuery<Item & { rubric?: Record<string, unknown> }>(`content/items/${selected?.item_id}`, !!selected && !selected.item && !!workspace?.permissions.includes('content'))
  const reviewItem = selected?.item || item.data
  const [score, setScore] = useState('')
  const [feedback, setFeedback] = useState('')
  const [dimensions, setDimensions] = useState('{}')
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  return <div className={styles.grid}><section className={styles.panel}><h2>Responses awaiting review</h2>{saved && <Status>Expert evaluation recorded and evidence recalculated.</Status>}{queue.isPending ? <Loading /> : queue.error ? <ErrorNotice error={queue.error} retry={() => queue.refetch()} /> : !queue.data?.length ? <p className={styles.muted}>No responses currently require expert review.</p> : <ul className={styles.list}>{queue.data.map((attempt, i) => <li key={attempt.id}><Button variant="link" className="h-auto justify-start whitespace-normal p-0 text-start"  onClick={async () => { try { const detail = await ttApi<Attempt>(`attempts/${attempt.id}`); setSelected({ ...detail, item: attempt.item }); setFeedback(''); setScore(''); setDimensions('{}'); setSaved(false); setError(null) } catch (e) { setError(e as Error) } }}>Response {i + 1} · {attempt.phase.replace(/_/g, ' ')}</Button><p>{attempt.created_at}</p></li>)}</ul>}</section><section className={styles.panel}>{selected ? <><h2>Evaluate this response</h2>{!reviewItem && item.isFetching ? <Loading /> : item.error ? <ErrorNotice error={item.error} /> : reviewItem && <><h3>{reviewItem.title}</h3><Markdown>{reviewItem.prompt}</Markdown>{reviewItem.rubric && <details className={styles.details}><summary>Approved rubric</summary><pre>{JSON.stringify(reviewItem.rubric, null, 2)}</pre></details>}</>}<h3>Learner response</h3><pre className={styles.response}>{JSON.stringify(selected.response, null, 2)}</pre><form className={styles.form} onSubmit={async e => { e.preventDefault(); setBusy(true); setError(null); try { await ttPost(`attempts/${selected.id}/evaluations`, { score: Number(score), feedback, dimensions: JSON.parse(dimensions) }); setSelected(null); setSaved(true); await queue.refetch() } catch (e) { setError(e as Error) } finally { setBusy(false) } }}><Field label="Criterion score (0–1)"><Input type="number" min={0} max={1} step={0.01} required value={score} onChange={e => setScore(e.target.value)} /></Field><Field label="Evidence-based feedback"><Textarea required value={feedback} onChange={e => setFeedback(e.target.value)} maxLength={6000} rows={6} /></Field><Field label="Dimension scores JSON"><Textarea value={dimensions} onChange={e => setDimensions(e.target.value)} className={styles.codeInput} /></Field><Button  disabled={busy}>Record expert evaluation</Button></form></> : <><h2>A careful second look</h2><p className={styles.muted}>Select a pending response. Review the task and rubric, then record a criterion score and feedback grounded in the learner’s response.</p></>}<ErrorNotice error={error} /></section></div>
}
