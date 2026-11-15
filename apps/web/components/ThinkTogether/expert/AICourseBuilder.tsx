'use client'

import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import { Label } from '@components/ui/label'
import { Textarea } from '@components/ui/textarea'
import { ttApi, ttPost, useTTQuery } from '@services/thinktogether/api'
import { ErrorNotice, Loading } from '../Shared'
import { styles } from '../Shell'
import CourseMarkdown from '../CourseMarkdown'

interface CourseMaterial { id: string; name: string; pages: number; characters: number }

export interface GenerationRun {
  id: string
  title: string
  status: string
  stage: string
  program_id?: string
  completed_steps: number
  total_steps: number
  revision: number
  error_type?: string
  counts: { lessons?: number; practice_items?: number; mock_items?: number }
  modules: { skill_id: string; code: string; title: string; status: string; lesson_id?: string; items?: number }[]
  events: { id: number; message: string; stage: string }[]
  chapters?: { title: string; markdown?: string; lesson_id?: string; csv?: string; status: string }[]
  course_format?: string
}

export function generationRunning(run?: GenerationRun) {
  return !!run && !['done', 'dead'].includes(run.status)
}

export default function AICourseBuilder() {
  const client = useQueryClient()
  const runs = useTTQuery<GenerationRun[]>('content/generations')
  const materials = useTTQuery<CourseMaterial[]>('content/materials')
  const [selected, setSelected] = useState<string | null>(null)
  const [form, setForm] = useState({ title: 'PMP preparation demo', audience: 'Adults learning this topic from the beginning', language: 'en', weeks: 8, brief: '', practice_items_per_skill: 5, course_format: 'demo_three_chapters', material_ids: [] as string[], material_text: '' })
  const [uploading, setUploading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [evidence, setEvidence] = useState('')
  const [reviewed, setReviewed] = useState(false)
  const request = useRef<{ body: string; key: string } | null>(null)
  const runId = selected || runs.data?.[0]?.id
  const path = runId ? `content/generations/${runId}` : ''
  const state = useQuery({ queryKey: ['thinktogether', path], queryFn: () => ttApi<GenerationRun>(path), enabled: !!runId,
    refetchInterval: query => generationRunning(query.state.data) ? 4000 : false, retry: false })
  const run = state.data

  useEffect(() => {
    if (!runId || !generationRunning(run)) return
    const stream = new EventSource(`/api/v1/tt/content/generations/${runId}/events`, { withCredentials: true })
    stream.addEventListener('progress', event => {
      try { client.setQueryData(['thinktogether', `content/generations/${runId}`], JSON.parse((event as MessageEvent).data)) } catch { /* HTTP polling retains recovery. */ }
    })
    stream.addEventListener('access_revoked', () => { stream.close(); void state.refetch() })
    stream.onerror = () => stream.close() // Auth refresh and recovery use the normal HTTP transport.
    return () => stream.close()
    // Reopen after a manual retry; checkpoints are retained across reconnects.
  }, [runId, run?.status, client]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!run?.program_id) return
    void client.invalidateQueries({ queryKey: ['thinktogether', 'content'] })
  }, [client, run?.program_id, run?.revision])

  async function act(task: () => Promise<GenerationRun>) {
    setBusy(true); setError(null)
    try {
      const result = await task()
      setSelected(result.id)
      client.setQueryData(['thinktogether', `content/generations/${result.id}`], result)
      await client.invalidateQueries({ queryKey: ['thinktogether', 'content/generations'] })
      await client.invalidateQueries({ queryKey: ['thinktogether', 'content'] })
      setReviewed(false)
    } catch (failure) { setError(failure as Error) } finally { setBusy(false) }
  }

  async function create() {
    const body = JSON.stringify(form)
    if (!request.current || request.current.body !== body) request.current = { body, key: crypto.randomUUID() }
    const key = request.current.key
    await act(async () => {
      const result = await ttPost<GenerationRun>('content/generations', { ...form, idempotency_key: key })
      request.current = null
      return result
    })
  }

  async function upload(files: FileList | null) {
    if (!files?.length) return
    setUploading(true); setError(null)
    try {
      if (files.length + form.material_ids.length > 5) throw new Error('Attach up to five materials per course.')
      for (const file of Array.from(files)) {
        if (file.size > 10 * 1024 * 1024) throw new Error('Choose files smaller than 10 MB each.')
        const data = new FormData(); data.append('file', file)
        const saved = await ttApi<CourseMaterial>('content/materials', { method: 'POST', body: data })
        setForm(current => ({ ...current, material_ids: [...new Set([...current.material_ids, saved.id])] }))
        await materials.refetch()
      }
    } catch (failure) { setError(failure as Error) } finally { setUploading(false) }
  }

  function downloadCsv(csv: string, index: number) {
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a'); link.href = url; link.download = `chapter-${index+1}-exercises.csv`; link.click(); URL.revokeObjectURL(url)
  }

  if (runs.isPending) return <section className={styles.panel} aria-label="AI course builder"><h2>AI course builder</h2><Loading /></section>
  if (runs.error) return <section className={styles.panel} aria-label="AI course builder"><h2>AI course builder</h2><ErrorNotice error={new Error('Course generation could not be loaded.')} retry={() => runs.refetch()} /></section>

  return <section className={styles.panel} aria-label="AI course builder">
    <h2>Create a course</h2>
    <p className={styles.muted}>Describe a topic, paste notes or attach materials. AI creates a three-chapter demo with lessons and exercises. Review and approve it before publishing.</p>
    <ErrorNotice error={error || runs.error || state.error} />
    <form className="mt-4 space-y-4" onSubmit={event => { event.preventDefault(); void create() }}>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2"><Label htmlFor="ai-course-title">Course title</Label><Input id="ai-course-title" value={form.title} maxLength={200} required onChange={event => setForm({ ...form, title: event.target.value })} /></div>
        <div className="space-y-2"><Label htmlFor="ai-course-audience">Audience</Label><Input id="ai-course-audience" value={form.audience} maxLength={2000} onChange={event => setForm({ ...form, audience: event.target.value })} /></div>
        <div className="space-y-2"><Label htmlFor="ai-course-language">Language</Label><select id="ai-course-language" className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm" value={form.language} onChange={event => setForm({ ...form, language: event.target.value })}><option value="en">English</option><option value="ru">Русский</option></select></div>
        <div className="space-y-2"><Label>Demo format</Label><p className="text-sm text-muted-foreground">3 chapters · tagged Markdown · CSV exercises</p></div>
      </div>
      <div className="space-y-2"><Label htmlFor="ai-course-brief">Topic and requirements</Label><Textarea id="ai-course-brief" rows={3} maxLength={4000} value={form.brief} onChange={event => setForm({ ...form, brief: event.target.value })} placeholder="What should the course teach? Any examples or learning goals?" /></div>
      <details className="space-y-3 rounded-md border p-3"><summary className="cursor-pointer text-sm font-medium">Add materials or paste text (optional)</summary><div className="space-y-2 pt-2"><Label htmlFor="ai-course-files">Attach files</Label><Input id="ai-course-files" type="file" multiple accept=".pdf,.docx,.txt,.md,.csv,.json" disabled={uploading} onChange={event => { void upload(event.target.files); event.target.value = '' }} /><p className="text-xs text-muted-foreground">PDF, DOCX, text, Markdown, CSV or JSON · up to 10 MB each. Scanned pages need OCR first. Selected text is sent to the course AI.</p></div>{uploading && <p role="status" className="text-sm">Reading materials…</p>}{materials.error && <ErrorNotice error={materials.error} retry={() => materials.refetch()} />}{!!materials.data?.length && <fieldset className="space-y-2"><legend className="text-sm font-medium">Use existing materials</legend>{materials.data.map(material => <label className="flex items-center gap-2 text-sm" key={material.id}><input type="checkbox" checked={form.material_ids.includes(material.id)} disabled={!form.material_ids.includes(material.id) && form.material_ids.length >= 5} onChange={event => setForm(current => ({ ...current, material_ids: event.target.checked ? [...current.material_ids, material.id] : current.material_ids.filter(id => id !== material.id) }))} /><span>{material.name} · {material.characters.toLocaleString()} characters</span></label>)}</fieldset>}<div className="space-y-2"><Label htmlFor="ai-course-text">Paste text or notes</Label><Textarea id="ai-course-text" rows={5} maxLength={50000} value={form.material_text} onChange={event => setForm({ ...form, material_text: event.target.value })} /></div></details>
      <p className="text-sm text-muted-foreground">Chapters appear as they are created. Closing this page does not stop generation.</p>
      <Button disabled={busy || uploading} type="submit">{busy ? 'Working…' : 'Create course'}</Button>
    </form>

    {!!runs.data?.length && <div className="mt-6 space-y-2"><Label htmlFor="ai-course-runs">Generation history</Label><select id="ai-course-runs" className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm" value={runId || ''} onChange={event => { setSelected(event.target.value); setEvidence(''); setReviewed(false); setError(null) }}>{runs.data.map(item => <option key={item.id} value={item.id}>{item.title} · {item.stage.replaceAll('_', ' ')}</option>)}</select></div>}
    {run && <div className="mt-5 space-y-4" aria-label="Course generation progress">
      <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-medium">{run.title}</h3><span className="text-sm text-muted-foreground">{run.stage.replaceAll('_', ' ')} · {run.completed_steps}/{run.total_steps}</span></div>
      <progress className="h-2 w-full accent-neutral-900" max={run.total_steps} value={run.completed_steps} aria-label="Saved course generation steps" />
      <p className="text-sm text-muted-foreground">{run.counts.lessons || 0} lessons · {run.counts.practice_items || 0} practice cases · {run.counts.mock_items || 0} mock questions saved</p>
      {run.status === 'retry' && run.error_type && <p role="status" className="text-sm">The last step could not be completed. It will retry automatically; saved drafts are safe.</p>}
      {run.status === 'dead' && <p role="alert" className="text-sm">Generation needs attention. Check the AI provider configuration, then retry from the saved step.</p>}
      <ol role="log" aria-label="Generation updates" aria-live="polite" className="space-y-1 text-sm">{run.events.slice(-4).map(event => <li key={event.id}>{event.message}</li>)}</ol>
      {!!run.modules.length && <details className="rounded-md border p-3"><summary className="cursor-pointer text-sm font-medium">Program outline and saved lessons</summary><ol className="mt-3 space-y-2 text-sm">{run.modules.map(module => <li key={module.skill_id} className="flex justify-between gap-3"><span>{module.title}</span><span className="shrink-0 text-muted-foreground">{module.status === 'draft' ? `${module.items} cases · draft` : 'Queued'}</span></li>)}</ol></details>}
      {!!run.chapters?.length && <div className="space-y-4">{run.chapters.map((chapter, index) => <details className="rounded-lg border p-4" key={index}><summary className="cursor-pointer font-medium">Chapter {index+1}: {chapter.title}</summary><div className="mt-4 space-y-4">{chapter.markdown ? <CourseMarkdown lessonId={chapter.lesson_id}>{chapter.markdown}</CourseMarkdown> : <p className="text-sm text-muted-foreground">Queued for the writing agent.</p>}{chapter.csv && <Button variant="outline" size="sm" onClick={() => downloadCsv(chapter.csv!, index)}>Download exercise CSV</Button>}</div></details>)}</div>}
      <div className="flex flex-wrap gap-2">{generationRunning(run) && <Button variant="outline" disabled={busy} onClick={() => void act(() => ttPost(`${path}/cancel`))}>Stop after current step</Button>}{run.status === 'dead' && <Button variant="outline" disabled={busy} onClick={() => void act(() => ttPost(`${path}/retry`))}>Retry saved step</Button>}{run.program_id && <Button variant="outline" onClick={() => document.getElementById('content-inventory')?.scrollIntoView({ behavior: 'smooth' })}>Review saved drafts below</Button>}</div>
      {['ready_for_review', 'approved'].includes(run.stage) && <div className="space-y-3 border-t pt-4">
        <h3 className="font-medium">Expert review</h3>
        <p className="text-sm text-muted-foreground">Inspect and edit the saved lessons, questions, keys and explanations below. Approval applies to the complete package; publishing is a separate action.</p>
        <Label htmlFor="ai-course-evidence">Review findings and approval reference</Label>
        <Textarea id="ai-course-evidence" value={evidence} rows={3} maxLength={6000} onChange={event => setEvidence(event.target.value)} />
        <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} className="mt-0.5" />{run.stage === 'approved' ? 'I confirm that the approved package should be published.' : 'I have checked the content, answer keys, explanations and exam coverage.'}</label>
        <div className="flex flex-wrap gap-2"><Button disabled={busy || !reviewed || evidence.trim().length < 20} onClick={() => void act(() => ttPost(`${path}/review/${run.stage === 'approved' ? 'publish' : 'approve'}`, { evidence }))}>{run.stage === 'approved' ? 'Publish approved course' : 'Approve reviewed package'}</Button>{run.stage === 'approved' && <Button variant="outline" disabled={busy || evidence.trim().length < 20} onClick={() => void act(() => ttPost(`${path}/review/request_changes`, { evidence }))}>Return to draft</Button>}</div>
      </div>}
      {run.stage === 'published' && <p className="text-sm font-medium" role="status">The expert-approved course is published.</p>}
    </div>}
  </section>
}
