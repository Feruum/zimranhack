import { createHash, timingSafeEqual } from 'node:crypto'
import type { IncomingMessage, ServerResponse } from 'node:http'
import * as Y from 'yjs'

export type AgreedNote = { room_uuid: string; board_uuid: string; event_sequence: number; note_id: string; text: string }
export const noteId = (room: string, sequence: number) => `tt-note:${room}:${sequence}`
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/

function validate(value: unknown): AgreedNote {
  const note = value as AgreedNote
  if (!note || typeof note !== 'object' || Object.keys(note).sort().join(',') !== 'board_uuid,event_sequence,note_id,room_uuid,text'
      || typeof note.room_uuid !== 'string' || !uuid.test(note.room_uuid)
      || typeof note.board_uuid !== 'string' || !note.board_uuid.startsWith('board_') || !uuid.test(note.board_uuid.slice(6))
      || !Number.isSafeInteger(note.event_sequence) || note.event_sequence <= 0
      || note.note_id !== noteId(note.room_uuid, note.event_sequence)
      || typeof note.text !== 'string' || !note.text.trim() || note.text.length > 700) {
    throw Error('Invalid agreed board note')
  }
  return note
}

export function appendAgreedNote(doc: Y.Doc, input: AgreedNote): boolean {
  const note = validate(input)
  const markers = doc.getMap<string>('thinktogether_notes')
  const fingerprint = createHash('sha256').update(JSON.stringify([note.room_uuid, note.board_uuid, note.event_sequence, note.text])).digest('hex')
  const previous = markers.get(note.note_id)
  if (previous) {
    if (previous !== fingerprint) throw Error('Note identifier already used with different content')
    return false
  }
  doc.transact(() => {
    const fragment = doc.getXmlFragment('default')
    const count = markers.size
    const block = new Y.XmlElement('noteBlock')
    // Match NoteBlockExtension's native attrs; no HTML or unknown node types.
    for (const [key, value] of Object.entries({ x: 100 + (count % 4) * 390, y: 100 + Math.floor(count / 4) * 270,
      width: 360, height: 240, color: 'blue', zIndex: 1 })) block.setAttribute(key, value as never)
    const paragraph = new Y.XmlElement('paragraph')
    const text = new Y.XmlText()
    text.insert(0, note.text)
    paragraph.insert(0, [text])
    block.insert(0, [paragraph])
    fragment.insert(fragment.length, [block])
    markers.set(note.note_id, fingerprint)
  }, 'thinktogether-agreed-note')
  return true
}

export async function handleBoardNoteRequest(request: IncomingMessage, response: ServerResponse,
  dependencies: { internalKey: string; write(note: AgreedNote): Promise<void> }): Promise<boolean> {
  if (request.url !== '/internal/tt/board-notes') return false
  const reply = (status: number, body: object) => { response.writeHead(status, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(body)) }
  const supplied = request.headers['x-internal-key']
  const expected = Buffer.from(dependencies.internalKey)
  const provided = Buffer.from(typeof supplied === 'string' ? supplied : '')
  if (!expected.length || provided.length !== expected.length || !timingSafeEqual(provided, expected)) {
    reply(403, { error: 'Invalid internal credential' }); return true
  }
  if (request.method !== 'POST') { reply(405, { error: 'POST required' }); return true }
  let note: AgreedNote
  try {
    const chunks: Buffer[] = []
    let size = 0
    for await (const chunk of request) {
      const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
      size += buffer.length
      if (size > 8192) throw Error('Payload too large')
      chunks.push(buffer)
    }
    note = validate(JSON.parse(Buffer.concat(chunks).toString('utf8')))
  } catch { reply(422, { error: 'Invalid agreed board note' }); return true }
  try {
    await dependencies.write(note)
    reply(200, { written: true, note_id: note.note_id })
  } catch { reply(503, { error: 'Shared board persistence is unavailable; retry the same note' }) }
  return true
}
