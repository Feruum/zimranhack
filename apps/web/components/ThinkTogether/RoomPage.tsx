'use client'
import { Button } from '@components/ui/button'
import { Textarea } from '@components/ui/textarea'
import { Label } from '@components/ui/label'
import { Badge } from '@components/ui/badge'

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useTTQuery, ttPost } from '@services/thinktogether/api'
import type { Room, RoomEvent } from '@services/thinktogether/types'
import { formatDate, humanize, mergeRoomEvents, PHASES } from '@services/thinktogether/helpers'
import { PageHeading, styles, useWorkspace } from './Shell'
import { ErrorNotice, Field, Loading, Markdown } from './Shared'
import RoomMedia from './RoomMedia'
import RoomTask from './RoomTask'

const phaseCopy: Record<string, string> = { preparation: 'Review the case and your role.', individual_attempt: 'Work independently. Shared discussion and the board are closed while you respond.', discussion: 'Compare answers and record agreed notes.', transfer: 'Apply what you learned to a fresh case on your own. Discussion and shared work pause here.', summary: 'Review the group summary.' }
export default function RoomPage({ roomId }: { roomId: string }) {
  const workspace = useWorkspace()
  const query = useTTQuery<Room>(`rooms/${roomId}`)
  const [events, setEvents] = useState<RoomEvent[]>([])
  const [connection, setConnection] = useState<'connecting' | 'connected' | 'reconnecting' | 'revoked'>('connecting')
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)
  const [chat, setChat] = useState('')
  const [summary, setSummary] = useState('')
  const [board, setBoard] = useState(false)
  const cursor = useRef(0)
  const chatEnd = useRef<HTMLDivElement>(null)
  const room = query.data
  useEffect(() => {
    cursor.current = 0
    setEvents([])
    const stream = new EventSource(`/api/v1/tt/rooms/${encodeURIComponent(roomId)}/events?after=${cursor.current}`, { withCredentials: true })
    stream.onopen = () => setConnection('connected')
    stream.onerror = () => setConnection('reconnecting')
    stream.addEventListener('access_revoked', () => { stream.close(); setConnection('revoked'); setBoard(false); query.refetch() })
    stream.addEventListener('room_event', message => {
      try { const event = JSON.parse((message as MessageEvent).data) as RoomEvent; cursor.current = Math.max(cursor.current, event.id); setEvents(previous => mergeRoomEvents(previous, [event])); if (event.kind !== 'chat' && event.kind !== 'agent_message') query.refetch() } catch { setError(new Error('A session update could not be read. Refresh the room to continue.')) }
    })
    return () => stream.close()
    // The stream owns replay; query.refetch is stable and the room identity owns its lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId, room?.phase])
  useEffect(() => { chatEnd.current?.scrollIntoView({ block: 'nearest' }) }, [events.length])
  async function command(action: string, payload: Record<string, unknown> = {}, expectedRevision?: number) {
    if (!room) return
    setBusy(true); setError(null)
    try { await ttPost(`rooms/${roomId}/commands`, { command_id: crypto.randomUUID(), expected_revision: expectedRevision ?? room.revision, action, payload }); await query.refetch() } catch (e) { setError(e as Error); await query.refetch(); throw e } finally { setBusy(false) }
  }
  if (query.isPending) return <Loading>Opening your session…</Loading>
  if (!room) return <ErrorNotice error={query.error} retry={() => query.refetch()} />
  const privatePhase = room.phase === 'individual_attempt' || room.phase === 'transfer'
  const audio = [...events].reverse().find(e => e.kind === 'consent_audio' && e.payload.author === workspace?.user.id)?.payload.granted || false
  const messages = privatePhase ? [] : events.filter(e => e.kind === 'chat' || e.kind === 'agent_message')
  const approvedSummary = [...events].reverse().find(e => e.kind === 'approve_board_summary')?.payload.approved_board_summary
  const agreedNotes = new Set(events.filter(e => e.kind === 'approve_agent_note').map(e => (e.payload as { event_sequence?: number }).event_sequence))
  const operator = workspace?.permissions.includes('groups')
  const role = room.roles[workspace?.user.id || '']
  return <><PageHeading  title={room.title} description={`${formatDate(room.starts_at)} · ${room.duration_minutes} minutes${role ? ` · Your role: ${humanize(role)}` : ''}`} action={<Button asChild variant="outline"><Link  href="/thinktogether/group">Back to my group</Link></Button>} /><ol className={styles.phases} aria-label="Session phases">{PHASES.map((phase, i) => <li key={phase} aria-current={room.phase === phase ? 'step' : undefined} className={room.phase === phase ? styles.phaseActive : ''}><span>{i + 1}</span>{humanize(phase)}</li>)}</ol><section className={styles.notice}><h2>{humanize(room.phase)}</h2><p>{phaseCopy[room.phase]}</p>{operator && room.phase !== 'summary' && <Button variant="outline"  disabled={busy} onClick={() => command('advance').catch(() => undefined)}>Advance to {humanize(PHASES[PHASES.indexOf(room.phase as typeof PHASES[number]) + 1])}</Button>}</section><ErrorNotice error={error || query.error} />{connection === 'reconnecting' && <p className={styles.muted} role="status">Reconnecting to session updates… Saved messages will be replayed.</p>}{connection === 'revoked' ? <div className={styles.error} role="alert">Your access to this session has ended. Return to your group to check your membership and access.</div> : <div className={styles.grid}><div className={styles.stack}>
    {privatePhase && <RoomTask key={`${room.id}-${room.phase}`} roomId={roomId} phase={room.phase} />}<RoomMedia key={`${room.id}-${privatePhase || room.phase === 'summary' ? 'closed' : 'open'}`} roomId={roomId} enabled={!privatePhase && room.phase !== 'summary'} />
    {room.case && !privatePhase && <section className={styles.panel}><h2>{room.case.title}</h2><Markdown>{room.case.prompt}</Markdown>{room.case.choices?.map(c => <p key={c.id}>{c.text}</p>)}<p className={styles.muted}>Group work does not count as independent mastery.</p></section>}
    <section className={styles.panel}><div className={styles.panelHeader}><h2>Group board</h2>{!privatePhase && <Button variant="outline"  onClick={() => setBoard(!board)}>{board ? 'Close board' : 'Open board'}</Button>}</div>{privatePhase ? <p className={styles.muted}>The shared board is closed while you work independently.</p> : board ? <div className={styles.board}><iframe src={`/board/${encodeURIComponent(room.board_uuid)}?embedded=1`} title="Collaborative group board" allow="fullscreen" /></div> : <p className={styles.muted}>{room.phase === 'summary' ? 'Revisit the group’s ideas. The board is read-only in this phase.' : 'Open the board to work with your group.'}</p>}</section>
    {approvedSummary && <section className={styles.panel}><h2>Session summary</h2><Markdown>{approvedSummary}</Markdown></section>}
    {operator && <section className={styles.panel}><h2>Facilitator summary</h2><Field label="Approved group summary"><Textarea value={summary} onChange={e => setSummary(e.target.value)} maxLength={8000} rows={5} /></Field><div className={styles.actions}><Button variant="outline"  disabled={busy || !summary.trim()} onClick={() => command('approve_board_summary', { text: summary }).then(() => setSummary('')).catch(() => undefined)}>Approve summary for the agent</Button></div></section>}
  </div><aside className={styles.stack}><section className={styles.panel}><h2>Group conversation</h2><div className={styles.chatLog} role="log" aria-label="Session messages">{messages.length ? messages.map(event => <article key={event.id} className={event.kind === 'agent_message' ? styles.agentMessage : styles.chatMessage}><strong>{event.kind === 'agent_message' ? 'Learning facilitator' : event.payload.author === workspace?.user.id ? 'You' : 'Group member'}</strong><p>{event.payload.text}</p>{operator && room.phase === 'discussion' && (event.payload as { facilitation_action?: string }).facilitation_action === 'propose_note' && <Button variant="outline"  disabled={busy || agreedNotes.has(event.id)} onClick={() => command('approve_agent_note', { event_sequence: event.id }, Math.max(room.revision, cursor.current)).catch(() => undefined)}>{agreedNotes.has(event.id) ? 'Agreed note queued for the board' : 'Approve agreed note for board'}</Button>}</article>) : <p className={styles.muted}>No messages yet.</p>}<div ref={chatEnd} /></div><form onSubmit={async e => { e.preventDefault(); try { await command('chat', { text: chat }); setChat('') } catch { /* Error remains visible with the draft intact. */ } }}><Field label="Message your group"><Textarea value={chat} onChange={e => setChat(e.target.value)} maxLength={4000} required disabled={privatePhase} placeholder={privatePhase ? 'Conversation pauses for independent work.' : 'Message your group'} /></Field><div className={styles.actions}><Button  disabled={privatePhase || busy || !chat.trim()}>Send message</Button></div></form></section><section className={styles.panel}><h2>AI voice facilitator</h2><p className={styles.muted}>If you consent, your session speech may be transcribed and processed to support the group’s discussion. Transcripts are scheduled for deletion after 30 days. You can withdraw consent at any time.</p><Label className={styles.choice}><input className="mt-0.5 size-4 shrink-0 accent-primary" type="checkbox" checked={audio} disabled={busy} onChange={e => command('consent_audio', { granted: e.target.checked, consent_version: 'audio-v1' }).catch(() => undefined)} /><span>I agree to audio processing for this session.</span></Label><Badge variant="secondary" >{room.agent_enabled ? 'Facilitator enabled' : 'Facilitator disabled'}</Badge>{(operator || workspace?.permissions.includes('support')) && room.agent_enabled && <div className={styles.actions}><Button variant="outline"  disabled={busy} onClick={() => command('disable_agent').catch(() => undefined)}>Disable AI facilitator</Button></div>}</section></aside></div>}</>
}
