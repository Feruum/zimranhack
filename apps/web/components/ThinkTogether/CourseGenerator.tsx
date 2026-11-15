'use client'

import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import { Label } from '@components/ui/label'
import { ttApi, ttPost, useTTQuery } from '@services/thinktogether/api'
import { ErrorNotice } from './Shared'
import { styles } from './Shell'

interface CourseRun {
  id: string
  title: string
  status: string
  created_at: string
  stage: string
  error?: string
  markdown: string | null
  draft_url?: string
  download_url?: string
  coverage: { message: string; selected_chunks: number; total_chunks: number; partial: boolean }
  diagrams: { id: string; label: string; url: string }[]
}

const pending = (run?: CourseRun) => !!run && ['pending', 'running', 'retry'].includes(run.status)

export default function CourseGenerator() {
  const client = useQueryClient()
  const runs = useTTQuery<CourseRun[]>('content/course-pipeline')
  const [selected, setSelected] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const submission = useRef<{ file: File; title: string; materialId?: string; key: string } | null>(null)
  const identifier = selected || runs.data?.[0]?.id
  const path = `content/course-pipeline/${identifier}`
  const status = useQuery({ queryKey: ['thinktogether', path], queryFn: () => ttApi<CourseRun>(path),
    enabled: !!identifier, retry: false, refetchInterval: query => pending(query.state.data) ? 2500 : false })
  const run = status.data
  const allowedImages = new Set(run?.diagrams.map(diagram => diagram.url))

  async function generate() {
    if (!file || !title.trim()) return
    setBusy(true); setError(null)
    try {
      if (file.size > 10 * 1024 * 1024 || !file.size) throw new Error('Выберите непустой файл размером до 10 МБ.')
      if (submission.current?.file !== file || submission.current.title !== title.trim()) {
        submission.current = { file, title: title.trim(), key: crypto.randomUUID() }
      }
      const request = submission.current
      if (!request.materialId) {
        const form = new FormData()
        form.append('file', file)
        const material = await ttApi<{ id: string }>('content/materials', { method: 'POST', body: form })
        request.materialId = material.id
      }
      const result = await ttPost<CourseRun>('content/course-pipeline', {
        title: request.title, material_id: request.materialId, idempotency_key: request.key,
      })
      client.setQueryData(['thinktogether', `content/course-pipeline/${result.id}`], result)
      setSelected(result.id)
      submission.current = null
      await client.invalidateQueries({ queryKey: ['thinktogether', 'content/course-pipeline'] })
    } catch (failure) { setError(failure instanceof Error ? failure : new Error('Не удалось создать курс.')) }
    finally { setBusy(false) }
  }

  return <section className={`${styles.panel} space-y-5`} aria-labelledby="document-course-title">
    <div><h2 id="document-course-title">Мини-курс из документа</h2>
      <p className={styles.muted}>Три главы с объяснениями, практикой и схемами. Курс создаётся на русском языке.</p></div>
    <form className="space-y-4" onSubmit={event => { event.preventDefault(); void generate() }}>
      <div className="space-y-2"><Label htmlFor="course-source">Учебный материал</Label>
        <Input id="course-source" type="file" accept=".pdf,.txt,.md,.docx,.pptx" required disabled={busy}
          onChange={event => setFile(event.target.files?.[0] || null)} aria-describedby="course-source-help" />
        <p id="course-source-help" className="text-sm text-muted-foreground">PDF с текстовым слоем, TXT, Markdown, DOCX или PPTX. До 10 МБ. Для больших документов используется подборка фрагментов по теме.</p></div>
      <div className="space-y-2"><Label htmlFor="course-topic">Тема курса</Label>
        <Input id="course-topic" value={title} onChange={event => setTitle(event.target.value)} required maxLength={200} disabled={busy} /></div>
      <Button disabled={busy || !file || !title.trim()}>{busy ? 'Загрузка материала…' : 'Создать мини-курс'}</Button>
    </form>
    <ErrorNotice error={error || runs.error || status.error} />
    {!!runs.data?.length && <div className="space-y-2"><Label htmlFor="course-history">Созданные курсы</Label>
      <select id="course-history" className="w-full rounded-md border bg-background p-2" value={identifier || ''} onChange={event => setSelected(event.target.value)}>
        {runs.data.map(item => <option key={item.id} value={item.id}>{item.title} · {new Date(item.created_at).toLocaleString('ru-RU')}</option>)}
      </select></div>}
    {run && <div className="space-y-4">
      <p role="status" aria-live="polite">{pending(run) ? (run.stage === 'course' ? 'Составляем три главы…' : 'Готовим схемы и проверяем курс…')
        : run.status === 'done' ? 'Курс готов к просмотру.' : 'Не удалось завершить курс.'}</p>
      <p className="text-sm text-muted-foreground">{run.coverage.message} Фрагментов: {run.coverage.selected_chunks} из {run.coverage.total_chunks}.</p>
      {run.error && <p role="alert" className="text-sm text-destructive">{run.error}</p>}
      <div className="flex flex-wrap gap-4">
        {run.download_url && <a className="underline" href={run.download_url} download="course.final.md">Скачать Markdown</a>}
        {run.draft_url && <a className="underline" href={run.draft_url} download="course.draft.md">Скачать черновик</a>}
      </div>
      {run.markdown && <article className={styles.prose}><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{
        img: ({ src, alt }) => typeof src === 'string' && allowedImages.has(src)
          ? <span className="block"><img src={src} alt={alt || ''} loading="lazy" className="h-auto w-full" /><span className="block text-sm text-muted-foreground">{alt}</span></span> : null,
      }}>{run.markdown}</ReactMarkdown></article>}
    </div>}
  </section>
}
