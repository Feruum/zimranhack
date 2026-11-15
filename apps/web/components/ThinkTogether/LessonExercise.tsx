'use client'
import { useRef, useState } from 'react'
import { Button } from '@components/ui/button'
import { ttPost } from '@services/thinktogether/api'
import type { Attempt, Delivery } from '@services/thinktogether/types'
import { ErrorNotice } from './Shared'
import TaskRenderer from './TaskRenderer'
import AttemptResult from './AttemptResult'

export default function LessonExercise({ lessonId, itemId }: { lessonId: string; itemId: string }) {
  const [delivery, setDelivery] = useState<Delivery | null>(null)
  const [result, setResult] = useState<Attempt | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)
  const key = useRef<string | null>(null)
  return <div className="space-y-3"><ErrorNotice error={error} />{result ? <AttemptResult attempt={result} /> : delivery ? <TaskRenderer item={delivery.item} pending={busy} submit={async response => {
    if (!key.current) key.current = crypto.randomUUID()
    setBusy(true)
    try { setResult(await ttPost<Attempt>('attempts', { item_id: itemId, delivery_id: delivery.delivery_id, response, idempotency_key: key.current })) } finally { setBusy(false) }
  }} /> : <Button variant="outline" disabled={busy} onClick={async () => {
    setBusy(true); setError(null)
    try { setDelivery(await ttPost<Delivery>(`lessons/${lessonId}/exercises/${itemId}`)) } catch (failure) { setError(failure as Error) } finally { setBusy(false) }
  }}>{busy ? 'Opening…' : 'Start exercise'}</Button>}<p className="text-xs text-muted-foreground">Lesson practice · does not establish independent mastery.</p></div>
}
