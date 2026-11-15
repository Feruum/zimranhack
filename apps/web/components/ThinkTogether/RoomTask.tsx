'use client'
import { Button } from '@components/ui/button'

import { useState } from 'react'
import { ttApi, ttPost } from '@services/thinktogether/api'
import type { Attempt, Delivery } from '@services/thinktogether/types'
import { styles } from './Shell'
import { ErrorNotice } from './Shared'
import TaskRenderer from './TaskRenderer'
import AttemptResult from './AttemptResult'

export default function RoomTask({ roomId, phase }: { roomId: string; phase: string }) {
  const [delivery, setDelivery] = useState<Delivery | null>(null)
  const [result, setResult] = useState<Attempt | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)
  async function open() {
    setBusy(true); setError(null)
    try {
      const task = await ttApi<Delivery & { submitted_attempt_id?: string | null }>(`rooms/${roomId}/task`)
      setDelivery(task)
      if (task.submitted_attempt_id) setResult(await ttApi<Attempt>(`attempts/${task.submitted_attempt_id}`))
    } catch (e) { setError(e as Error) } finally { setBusy(false) }
  }
  return <section className={styles.panel}><p className="text-sm text-muted-foreground">{phase === 'transfer' ? 'Apply it to a fresh case' : 'Make your own decision'}</p>{result ? <AttemptResult attempt={result} /> : delivery ? <TaskRenderer key={delivery.delivery_id} item={delivery.item} submit={async response => { const key = `tt-room-${delivery.delivery_id}`; let idempotency = sessionStorage.getItem(key); if (!idempotency) { idempotency = crypto.randomUUID(); sessionStorage.setItem(key, idempotency) }; setResult(await ttPost<Attempt>('attempts', { item_id: delivery.item.id, delivery_id: delivery.delivery_id, response, idempotency_key: idempotency })) }} /> : <><h2>{phase === 'transfer' ? 'Independent transfer' : 'Your individual attempt'}</h2><p className={styles.muted}>Work on your own. Your private response is saved separately from the group discussion.</p><Button  disabled={busy} onClick={open}>{busy ? 'Opening your case…' : 'Open my case'}</Button></>}<ErrorNotice error={error} retry={error ? open : undefined} /></section>
}
