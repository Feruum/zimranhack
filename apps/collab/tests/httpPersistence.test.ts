import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createBoardSnapshotWriter } from '../src/httpPersistence.js'
import { createBoardPersistence } from '../src/persistence.js'

const etag = `"${'a'.repeat(64)}"`

function writer(fetch: (url: string, options?: RequestInit) => Promise<Response>, invalidate = async (_board: string) => {}) {
  return createBoardSnapshotWriter({ apiUrl: 'http://api', internalKey: 'internal-key', request: fetch, invalidate })
}

test('the board writer GETs an authoritative ETag and sends it with the binary PUT', async () => {
  const calls: { url: string; method: string; headers: Headers; body: RequestInit['body'] }[] = []
  const invalidated: string[] = []
  const write = writer(async (url, options) => {
    calls.push({ url, method: options?.method ?? 'GET', headers: new Headers(options?.headers), body: options?.body })
    return new Response('', { status: 200, headers: { ETag: etag } })
  }, async board => { invalidated.push(board) })
  await write('board', Uint8Array.of(1, 255))
  assert.deepEqual(calls.map(call => call.method), ['GET', 'PUT'])
  assert.equal(calls[0].headers.get('X-Internal-Key'), 'internal-key')
  assert.equal(calls[1].headers.get('If-Match'), etag)
  assert.equal(calls[1].headers.get('Content-Type'), 'application/octet-stream')
  assert.deepEqual([...new Uint8Array(calls[1].body as ArrayBuffer)], [1, 255])
  assert.deepEqual(invalidated, ['board'])
})

test('the board writer refuses an unconditioned PUT when the API has no ETag', async () => {
  let calls = 0
  const write = writer(async () => { calls++; return new Response('', { status: 200 }) })
  await assert.rejects(write('board', Uint8Array.of(1)), /ETag/)
  assert.equal(calls, 1)
})

test('a conflicted queued snapshot stays dirty and retries with a fresh authoritative ETag', async () => {
  const freshEtag = `"${'b'.repeat(64)}"`
  let currentEtag = etag, conflict = true, invalidations = 0
  let retry: (() => Promise<void>) | undefined
  const versions: string[] = []
  const states: number[][] = []
  const write = writer(async (_url, options) => {
    if (options?.method !== 'PUT') return new Response('', { headers: { ETag: currentEtag } })
    const supplied = new Headers(options.headers).get('If-Match')!
    versions.push(supplied)
    if (conflict) {
      // Another request committed after this attempt read its version.
      currentEtag = freshEtag
      conflict = false
      return new Response('', { status: 409 })
    }
    assert.equal(supplied, currentEtag)
    states.push([...new Uint8Array(options.body as ArrayBuffer)])
    return new Response('', { status: 200 })
  }, async () => { invalidations++ })
  const queue = createBoardPersistence({
    write, delayMs: 5000,
    setTimer(callback) { retry = callback; return callback },
    clearTimer(timer) { if (retry === timer) retry = undefined },
  })
  await queue.schedule('board', Uint8Array.of(2))
  await retry!()
  assert.deepEqual(queue.getPendingState('board'), Uint8Array.of(2))
  assert.equal(invalidations, 0)
  await retry!()
  assert.equal(queue.getPendingState('board'), undefined)
  assert.deepEqual(versions, [etag, freshEtag])
  assert.deepEqual(states, [[2]])
  assert.equal(invalidations, 1)
})
