import assert from 'node:assert/strict'
import { test } from 'node:test'
import { Readable } from 'node:stream'
import * as Y from 'yjs'
import { Server } from '@hocuspocus/server'
import { appendAgreedNote, handleBoardNoteRequest, noteId } from '../src/thinktogether-notes.js'

const room = '12345678-1234-1234-1234-123456789abc'
const board = 'board_87654321-4321-4321-4321-cba987654321'
const payload = { room_uuid: room, board_uuid: board, event_sequence: 7, note_id: noteId(room, 7), text: '<script>alert(1)</script> is a literal group note.' }

test('agreed note uses the actual native Tiptap schema and durable dedup marker', () => {
  const doc = new Y.Doc()
  assert.equal(appendAgreedNote(doc, payload), true)
  assert.equal(appendAgreedNote(doc, payload), false)
  const fragment = doc.getXmlFragment('default')
  assert.equal(fragment.length, 1)
  const note = fragment.get(0) as Y.XmlElement
  assert.equal(note.nodeName, 'noteBlock')
  assert.deepEqual(note.getAttributes(), { x: 100, y: 100, width: 360, height: 240, color: 'blue', zIndex: 1 })
  const paragraph = note.get(0) as Y.XmlElement
  assert.equal(paragraph.nodeName, 'paragraph')
  assert.equal((paragraph.get(0) as Y.XmlText).toString(), payload.text)
  const reloaded = new Y.Doc()
  Y.applyUpdate(reloaded, Y.encodeStateAsUpdate(doc))
  assert.equal(appendAgreedNote(reloaded, payload), false)
  assert.throws(() => appendAgreedNote(doc, { ...payload, text: 'different content' }), /identifier/)
})

test('installed Hocuspocus direct connection persists the native note and deduplicates on reopen', async () => {
  const stored = new Map<string, Uint8Array>()
  const server = new Server({
    quiet: true,
    async onLoadDocument({ documentName, document }) {
      const state = stored.get(documentName)
      if (state) Y.applyUpdate(document, state)
    },
    async onStoreDocument({ documentName, document }) { stored.set(documentName, Y.encodeStateAsUpdate(document)) },
  })
  try {
    const first = await server.hocuspocus.openDirectConnection(`board:${board}`)
    await first.transact(document => { assert.equal(appendAgreedNote(document, payload), true) })
    await first.disconnect()
    assert.ok(stored.has(`board:${board}`))
    const retry = await server.hocuspocus.openDirectConnection(`board:${board}`)
    await retry.transact(document => {
      assert.equal(appendAgreedNote(document, payload), false)
      assert.equal(document.getXmlFragment('default').length, 1)
    })
    await retry.disconnect()
  } finally { await server.destroy() }
})

async function invoke(key: string, body: unknown, write = async (_data: typeof payload) => undefined) {
  const request = Object.assign(Readable.from([JSON.stringify(body)]), { url: '/internal/tt/board-notes', method: 'POST', headers: { 'x-internal-key': key } })
  let status = 0, responseBody = ''
  const response = { writeHead(code: number) { status = code }, end(value: string) { responseBody = value } }
  await handleBoardNoteRequest(request as never, response as never, { internalKey: 'secret-internal', write })
  return { status, body: JSON.parse(responseBody) }
}

test('internal endpoint rejects missing secret, wrong room identifiers and oversized payloads', async () => {
  let writes = 0
  const write = async () => { writes++ }
  assert.equal((await invoke('', payload, write)).status, 403)
  assert.equal((await invoke('wrong', payload, write)).status, 403)
  assert.equal((await invoke('secret-internal', { ...payload, room_uuid: 'another-room' }, write)).status, 422)
  assert.equal((await invoke('secret-internal', { ...payload, note_id: noteId(room, 8) }, write)).status, 422)
  assert.equal((await invoke('secret-internal', { ...payload, text: 'a'.repeat(701) }, write)).status, 422)
  assert.equal(writes, 0)
})

test('endpoint acknowledges only successful durable writes and permits safe retry', async () => {
  assert.equal((await invoke('secret-internal', payload, async () => { throw Error('storage unavailable') })).status, 503)
  assert.equal((await invoke('secret-internal', payload)).status, 200)
})
