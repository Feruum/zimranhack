'use client'
import { Button } from '@components/ui/button'
import { Badge } from '@components/ui/badge'

import { useEffect, useRef, useState } from 'react'
import { ttApi, ttPost } from '@services/thinktogether/api'
import type { MockRun } from '@services/thinktogether/types'
import { formatDate, responseLabel } from '@services/thinktogether/helpers'
import { styles } from './Shell'
import { ErrorNotice, Markdown } from './Shared'
import TaskRenderer from './TaskRenderer'

export default function MockAssessment({ run, onUpdate, onClose }: { run: MockRun; onUpdate: (_run: MockRun) => void; onClose: () => void }) {
  const [index, setIndex] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [confirm, setConfirm] = useState(false)
  const expired = useRef(false)
  const remaining = Math.max(0, Math.ceil((new Date(run.deadline).getTime() - now) / 1000))
  const question = run.questions[index]
  async function refresh() { const data = await ttApi<MockRun>(`mock-runs/${run.id}`); onUpdate(data) }
  useEffect(() => { if (run.status !== 'in_progress') return; const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer) }, [run.status])
  useEffect(() => {
    if (run.status === 'in_progress' && remaining === 0 && !expired.current) { expired.current = true; ttApi<MockRun>(`mock-runs/${run.id}`).then(onUpdate).catch(setError) }
  }, [remaining, run.id, run.status, onUpdate])
  async function finalize() { setBusy(true); setError(null); try { onUpdate(await ttPost<MockRun>(`mock-runs/${run.id}/finalize`)); setConfirm(false) } catch (e) { setError(e as Error) } finally { setBusy(false) } }
  return <section className={styles.panel}><div className={styles.panelHeader}><div><h2>{run.status === 'in_progress' ? `${run.answered} of ${run.total} answers saved` : run.status === 'needs_review' ? 'Your mock is awaiting evaluation' : 'Your mock report'}</h2></div>{run.status === 'in_progress' && <span className={styles.timer} role="timer" aria-label="Time remaining">{Math.floor(remaining / 60)}:{String(remaining % 60).padStart(2, '0')}</span>}</div><ErrorNotice error={error} />
    {run.status !== 'in_progress' && <div className={styles.notice}>{run.score === null ? <p>Some responses still need review. Your report will update after evaluation.</p> : <h3>{Math.round(run.score * 100)}% criterion score</h3>}<p>{run.result.notice || 'Practice criterion result; no official PMP pass prediction.'}</p><p>{run.answered} of {run.total} answered{run.result.unanswered ? ` · ${run.result.unanswered} unanswered` : ''}</p><Button variant="outline"  onClick={() => refresh().catch(setError)}>Refresh report</Button></div>}
    <nav className={styles.questionNav} aria-label="Mock questions">{run.questions.map((q, i) => <Button key={q.item.id} className={index === i ? styles.questionActive : ''} aria-label={`Question ${i + 1}${q.answered ? ', answer saved' : ''}`} aria-current={index === i ? 'step' : undefined} onClick={() => setIndex(i)}>{i + 1}{q.answered ? ' ✓' : ''}</Button>)}</nav>
    {question && <div className={styles.mockQuestion}>{run.status === 'in_progress' && !question.answered && remaining > 0 ? <TaskRenderer key={question.delivery_id} item={question.item} button="Save answer" submit={async response => { const key = `tt-mock-${question.delivery_id}`; let idempotency = sessionStorage.getItem(key); if (!idempotency) { idempotency = crypto.randomUUID(); sessionStorage.setItem(key, idempotency) }; await ttPost(`mock-runs/${run.id}/answers`, { item_id: question.item.id, response, idempotency_key: idempotency }); await refresh(); if (index < run.questions.length - 1) setIndex(index + 1) }} /> : <><h3>{question.item.title}</h3><Markdown>{question.item.prompt}</Markdown><Badge variant="secondary" >{question.answered ? 'Answer saved' : 'Unanswered'}</Badge>{question.response && <div className={styles.notice}><h4>Your answer</h4><p>{responseLabel(question.item, question.response)}</p></div>}{run.status === 'completed' && question.explanation && <><h4>Explanation</h4><Markdown>{question.explanation}</Markdown></>}</>}</div>}
    <div className={styles.actions}>{run.status === 'in_progress' ? confirm ? <div className={styles.notice}><p>Finalize this form? You have saved {run.answered} of {run.total} answers. You cannot add answers after finalizing.</p><Button  disabled={busy} onClick={finalize}>Finalize my mock</Button><Button variant="link" size="sm" className="h-auto px-0 text-xs"  onClick={() => setConfirm(false)}>Keep working</Button></div> : <Button variant="outline"  onClick={() => setConfirm(true)}>Finish and view report</Button> : null}<Button variant="link" size="sm" className="h-auto px-0 text-xs"  onClick={onClose}>{run.status === 'in_progress' ? 'Return to program (timer continues)' : 'Return to program'}</Button></div><p className={styles.muted}>Deadline: {formatDate(run.deadline)}. Saved answers are final.</p>
  </section>
}
