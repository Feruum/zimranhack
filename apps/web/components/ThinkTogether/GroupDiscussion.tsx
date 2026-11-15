'use client'
import { Label } from '@components/ui/label'
import { Textarea } from '@components/ui/textarea'
import { Button } from '@components/ui/button'


import { useId, useRef, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ttApi } from '@services/thinktogether/api'
import type { Cohort } from '@services/thinktogether/types'
import { formatDate } from '@services/thinktogether/helpers'
import { ErrorNotice, Loading, Markdown } from './Shared'
import { styles } from './Shell'

type Message = {
  id: number
  message_uuid: string
  text: string
  author: { id: string | null; name: string }
  created_at: string
}
type MessagePage = {
  messages: Message[]
  next_after: number
  has_more: boolean
  posting_blocked: string | null
  can_read: boolean
}

function CohortThread({ cohort }: { cohort: Cohort }) {
  const client = useQueryClient()
  const inputId = useId()
  const statusId = useId()
  const key = ['thinktogether', 'cohort-discussion', cohort.id]
  const [text, setText] = useState('')
  const [sent, setSent] = useState(false)
  // An uncertain network response retries the same payload with the same key.
  const pending = useRef<{ text: string; key: string } | null>(null)
  const thread = useQuery<MessagePage>({
    queryKey: key,
    queryFn: async ({ signal }) => {
      const previous = client.getQueryData<MessagePage>(key)
      let cursor = previous?.next_after || 0
      let page: MessagePage | undefined
      const messages = new Map((previous?.messages || []).map(message => [message.id, message]))
      // Each request is bounded. A large backlog continues on the next poll.
      for (let count = 0; count < 5; count++) {
        page = await ttApi<MessagePage>(`cohorts/${encodeURIComponent(cohort.id)}/messages?after=${cursor}&limit=100`, { signal })
        if (!page.can_read) return page
        page.messages.forEach(message => messages.set(message.id, message))
        cursor = page.next_after
        if (!page.has_more) break
      }
      return { ...page!, next_after: cursor, messages: [...messages.values()].sort((a, b) => a.id - b.id) }
    },
    refetchInterval: 5000,
    retry: false,
  })
  const send = useMutation({
    mutationFn: (body: { text: string; idempotency_key: string }) =>
      ttApi<Message>(`cohorts/${encodeURIComponent(cohort.id)}/messages`, { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: (message, body) => {
      client.setQueryData<MessagePage>(key, previous => previous && previous.can_read ? {
        ...previous,
        // Do not advance the polling cursor: another member's earlier message
        // may still need to be fetched before this newly sent one.
        messages: [...new Map([...previous.messages, message].map(item => [item.id, item])).values()].sort((a, b) => a.id - b.id),
      } : previous)
      pending.current = null
      setText(current => current.trim() === body.text ? '' : current)
      setSent(true)
      void thread.refetch()
    },
  })
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const content = text.trim()
    if (!content || content.length > 4000 || send.isPending || thread.data?.posting_blocked) return
    if (pending.current?.text !== content) pending.current = { text: content, key: crypto.randomUUID() }
    setSent(false)
    send.mutate({ text: content, idempotency_key: pending.current.key })
  }
  return <div className={styles.stack}>
    <p className={styles.muted}>A place for questions and reflections between sessions. Messages stay with your group. Keep answers to independent tasks private.</p>
    {thread.isPending ? <Loading>Loading your group discussion…</Loading> : thread.error ? <ErrorNotice error={thread.error} retry={() => thread.refetch()} /> : <>
      {thread.data?.can_read === false ? <p className={styles.muted} role="status">{thread.data.posting_blocked}</p> : thread.data?.messages.length ? <ol className={styles.list} aria-label={`${cohort.title} discussion`}>
        {thread.data.messages.map(message => <li key={message.message_uuid} style={{ overflowWrap: 'anywhere' }}>
          <div className={styles.panelHeader}><strong>{message.author.name}</strong><time className={styles.muted} dateTime={message.created_at}>{formatDate(message.created_at, cohort.timezone)}</time></div>
          <Markdown>{message.text}</Markdown>
        </li>)}
      </ol> : <p className={styles.muted}>No messages yet. Start with a question or share what helped you learn.</p>}
      {thread.data?.has_more && <p role="status" className={styles.muted}>Loading more of the conversation…</p>}
    </>}
    <form onSubmit={submit} className={styles.stack} aria-label="Send a group message">
      <div className={styles.field}><Label htmlFor={inputId}>Message to {cohort.title}</Label>
      <Textarea id={inputId} value={text} rows={4} maxLength={4000} required aria-describedby={statusId}
        disabled={send.isPending || !!thread.error || !thread.data || !!thread.data.posting_blocked}
        onChange={event => { setText(event.target.value); setSent(false) }} /></div>
      <p id={statusId} className={styles.muted}>{thread.data?.posting_blocked || `${text.length} / 4,000 characters. Markdown is supported.`}</p>
      <ErrorNotice error={send.error} />
      {sent && <p className={styles.success} role="status">Message sent.</p>}
      <div className={styles.actions}><Button type="submit" 
        disabled={!text.trim() || send.isPending || !!thread.error || !thread.data || !!thread.data.posting_blocked}>
        {send.isPending ? 'Sending…' : send.isError ? 'Retry message' : 'Send message'}
      </Button></div>
    </form>
  </div>
}

export default function GroupDiscussion({ cohorts }: { cohorts: Cohort[] }) {
  const [selection, setSelection] = useState('')
  const selectId = useId()
  const cohort = cohorts.find(candidate => candidate.id === selection) || cohorts[0]
  return <section className={styles.panel}>
    <div className={styles.panelHeader}><h2>Between sessions</h2><Button asChild variant="outline"><a  href="/api/v1/tt/calendar?format=ics" download="thinktogether-calendar.ics">Export my calendar (.ics)</a></Button></div>
    <p className={styles.muted}>Your calendar includes sessions, due reviews and planned activities for the next 31 days.</p>
    {!cohort ? <p className={styles.muted}>Your group discussion will appear here once you join an active cohort.</p> : <>
      {cohorts.length > 1 && <div className={styles.field}><Label htmlFor={selectId}>Choose your group</Label><select className="h-10 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50" id={selectId} value={cohort.id} onChange={event => setSelection(event.target.value)}>{cohorts.map(candidate => <option key={candidate.id} value={candidate.id}>{candidate.title}</option>)}</select></div>}
      <CohortThread key={cohort.id} cohort={cohort} />
    </>}
  </section>
}
