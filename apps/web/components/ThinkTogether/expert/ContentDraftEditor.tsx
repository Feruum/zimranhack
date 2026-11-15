'use client'
import { useState } from 'react'
import { Button } from '@components/ui/button'
import { Textarea } from '@components/ui/textarea'
import { ttApi } from '@services/thinktogether/api'
import type { ContentRecord } from '@services/thinktogether/types'
import { ErrorNotice } from '../Shared'
import { styles } from '../Shell'

const editable: Record<string, string[]> = {
  programs: ['title', 'description'],
  lessons: ['title', 'body_markdown', 'objectives', 'estimated_minutes'],
  items: ['title', 'prompt', 'kind', 'choices', 'answer_key', 'rubric', 'explanation', 'hints', 'estimated_minutes', 'difficulty'],
}

export default function ContentDraftEditor({ resource, record, onClose, onSaved }: { resource: string; record: ContentRecord; onClose: () => void; onSaved: () => Promise<unknown> }) {
  const [value, setValue] = useState(() => JSON.stringify(Object.fromEntries((editable[resource] || []).filter(key => record[key] !== undefined).map(key => [key, record[key]])), null, 2))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  return <section id="content-draft-editor" className={styles.panel} aria-label="Edit content draft"><h2>Edit: {record.title}</h2><p className={styles.muted}>Edit the draft fields below. Existing approvals cannot be changed in place; return the package to draft first.</p><ErrorNotice error={error} /><form className="mt-3 space-y-3" onSubmit={async event => { event.preventDefault(); setBusy(true); setError(null); try { await ttApi(`content/drafts/${resource}/${record.id}`, { method: 'PATCH', body: JSON.stringify(JSON.parse(value)) }); await onSaved(); onClose() } catch (failure) { setError(failure as Error) } finally { setBusy(false) } }}><Textarea aria-label="Editable content JSON" rows={20} className="font-mono text-xs" value={value} onChange={event => setValue(event.target.value)} spellCheck={false} required /><div className="flex gap-2"><Button disabled={busy}>Save draft changes</Button><Button variant="outline" type="button" disabled={busy} onClick={onClose}>Cancel</Button></div></form></section>
}
