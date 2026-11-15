'use client'
import { Button } from '@components/ui/button'
import { Badge } from '@components/ui/badge'
import { SkiperLink } from '@components/ui/skiper-ui/css-link'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ArrowRight, CheckCircle2, BookOpen } from 'lucide-react'
import { useTTQuery, ttApi, ttPost } from '@services/thinktogether/api'
import type { Profile, Program, Lesson, MockForm, MockRun } from '@services/thinktogether/types'
import { EmptyState, PageHeading, styles } from './Shell'
import { ErrorNotice, Loading, Status, useTTMutation } from './Shared'
import MockAssessment from './MockAssessment'
import CourseMarkdown from './CourseMarkdown'

export default function ProgramPage({ initialLesson = '', initialMock = '' }: { initialLesson?: string; initialMock?: string }) {
  const profile = useTTQuery<Profile>('me/profile')
  const programs = useTTQuery<Program[]>('programs')
  const id = profile.data?.program_id
  const lessons = useTTQuery<Lesson[]>(`programs/${id}/lessons`, !!id)
  const forms = useTTQuery<MockForm[]>(`programs/${id}/mock-forms`, !!id)
  const runs = useTTQuery<{ id: string; status: string; deadline: string; score: number | null }[]>('me/mock-runs')
  const [selected, setSelected] = useState(initialLesson)
  const detail = useTTQuery<Lesson>(`programs/${id}/lessons/${selected}`, !!id && !!selected)
  const complete = useTTMutation<{ completed_at: string }>(`lessons/${selected}/complete`)
  const [run, setRun] = useState<MockRun | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  useEffect(() => { if (initialMock) ttApi<MockRun>(`mock-runs/${encodeURIComponent(initialMock)}`).then(setRun).catch(setError) }, [initialMock])
  const program = programs.data?.find(p => p.id === id)
  async function openRun(path: string, post = false) {
    setBusy(true); setError(null)
    try { const data = await (post ? ttPost<MockRun>(path) : ttApi<MockRun>(path)); setRun(data); const url = new URL(window.location.href); url.searchParams.set('mock', data.id); window.history.replaceState(null, '', url) } catch (e) { setError(e as Error) } finally { setBusy(false) }
  }
  return <><PageHeading  title={program?.title || 'My program'} description={program?.description} /><ErrorNotice error={profile.error || programs.error || error} />{profile.isPending || programs.isPending ? <Loading /> : !id ? <EmptyState title="Choose a program"><p>Set your program and exam date in your profile.</p><Button asChild><Link  href="/thinktogether/profile">Choose my program</Link></Button></EmptyState> : <>
    {program?.is_sample && <div className={styles.notice}><h2>Staff sample preview</h2><p>This original draft demonstrates the learning experience. It is not an approved complete PMP curriculum or a validated assessment.</p></div>}
    {run ? <MockAssessment run={run} onUpdate={setRun} onClose={() => { setRun(null); const url = new URL(window.location.href); url.searchParams.delete('mock'); window.history.replaceState(null, '', url); runs.refetch() }} /> : <div className={styles.grid}><section className={styles.stack}>{selected ? <article className={styles.panel}>{detail.isPending ? <Loading>Opening lesson…</Loading> : detail.error ? <ErrorNotice error={detail.error} retry={() => detail.refetch()} /> : detail.data && <><Button variant="link" size="sm" className="h-auto px-0 text-xs"  onClick={() => { setSelected(''); complete.reset(); const url = new URL(window.location.href); url.searchParams.delete('lesson'); window.history.replaceState(null, '', url) }}>← All lessons</Button><p className="text-sm text-muted-foreground">{detail.data.estimated_minutes} minute lesson</p>{detail.data.is_sample && <Badge variant="secondary" >Draft sample preview</Badge>}<h2 className={styles.lessonTitle}>{detail.data.title}</h2>{!!detail.data.objectives?.length && <div className={styles.notice}><h3>Learning objectives</h3><ul>{detail.data.objectives.map(o => <li key={o}>{o}</li>)}</ul></div>}<CourseMarkdown lessonId={detail.data.id}>{detail.data.body_markdown || ''}</CourseMarkdown><div className={styles.actions}><Button  disabled={complete.isPending} onClick={() => complete.mutate({})}><CheckCircle2 size={15} />Mark lesson complete</Button><Button asChild variant="outline"><Link  href="/thinktogether/practice">Apply this in practice</Link></Button></div><ErrorNotice error={complete.error} />{complete.isSuccess && <Status>Lesson completed.</Status>}</>}</article> : <section className={styles.panel}><div className={styles.panelHeader}><h2>Your curriculum</h2><BookOpen size={20} /></div>{lessons.isPending ? <Loading /> : lessons.error ? <><ErrorNotice error={lessons.error} retry={() => lessons.refetch()} /><SkiperLink className="text-sm font-medium"  href="/thinktogether/profile">Complete my profile and assessments</SkiperLink><div className={styles.actions}><Button asChild variant="outline"><Link  href="/thinktogether/billing">Check my access</Link></Button></div></> : !lessons.data?.length ? <p className={styles.muted}>No published lessons yet.</p> : <ol className={styles.activities}>{lessons.data.map((lesson, index) => <li key={lesson.id}><span className={styles.step}>{String(index + 1).padStart(2, '0')}</span><div><h3>{lesson.title}</h3><p>{lesson.estimated_minutes} minutes{lesson.completed || lesson.completed_at ? ' · completed' : ''}{lesson.is_sample ? ' · sample preview' : ''}</p></div><Button variant="outline" size="icon" className="shrink-0"  aria-label={`Read ${lesson.title}`} onClick={() => { setSelected(lesson.id); complete.reset(); const url = new URL(window.location.href); url.searchParams.set('lesson', lesson.id); window.history.replaceState(null, '', url) }}><ArrowRight size={17} /></Button></li>)}</ol>}</section>}</section><aside className={styles.stack}><section className={styles.panel}><h2>Timed mock assessments</h2><p className={styles.muted}>Work independently. Solutions appear after submission.</p>{forms.isPending ? <Loading /> : forms.error ? <ErrorNotice error={forms.error} retry={() => forms.refetch()} /> : !forms.data?.length ? <p className={styles.muted}>No mock exams available.</p> : <ul className={styles.list}>{forms.data.map(form => <li key={form.id}><h3>{form.title}</h3><p>{form.question_count} questions · {form.duration_minutes} minutes{form.is_sample ? ' · staff preview' : ''}</p><div className={styles.actions}><Button variant="outline"  disabled={busy} onClick={() => openRun(`mock-forms/${form.id}/runs`, true)}>Start or resume</Button></div></li>)}</ul>}</section><section className={styles.panel}><h2>My mock reports</h2>{runs.isPending ? <Loading /> : runs.error ? <ErrorNotice error={runs.error} /> : !runs.data?.length ? <p className={styles.muted}>No mock exam attempts yet.</p> : <ul className={styles.list}>{runs.data.map((r, index) => <li key={r.id}><Button variant="link" className="h-auto justify-start whitespace-normal p-0 text-start"  disabled={busy} onClick={() => openRun(`mock-runs/${r.id}`)}>Mock {index + 1} · {r.status === 'in_progress' ? 'Resume' : r.status === 'needs_review' ? 'Awaiting review' : 'View report'}</Button>{r.score !== null && <p>{Math.round(r.score * 100)}% criterion score</p>}</li>)}</ul>}</section></aside></div>}
  </>}</>
}
