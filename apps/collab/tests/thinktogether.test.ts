import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isBoardMutation, createThinkTogetherGuard } from '../src/thinktogether.js'
function packet(type: number, subtype = 0) {
  const address = new TextEncoder().encode('board:board_12345678-1234-1234-1234-123456789abc\0session')
  return new Uint8Array([address.length, ...address, type, subtype, 0])
}
test('sync step two and sync replies mutate but sync step one and awareness do not', () => {
  assert.equal(isBoardMutation(packet(0, 0)), false)
  assert.equal(isBoardMutation(packet(0, 1)), true)
  assert.equal(isBoardMutation(packet(0, 2)), true)
  assert.equal(isBoardMutation(packet(4, 1)), true)
  assert.equal(isBoardMutation(packet(1)), false)
  assert.throws(() => isBoardMutation(new Uint8Array([255])))
})
test('phase ACL rejects mutations and fails closed on revocation or API outages', async () => {
  const auth = { token: 'server-verified-user-token', boardUuid: 'board_123', verifiedAt: Date.now() }
  const guard = createThinkTogetherGuard(async () => ({ ok: true, json: async () => ({ managed: true, can_write: false }) }))
  await guard(auth, packet(0, 0))
  await assert.rejects(guard(auth, packet(0, 2)), /independent/)
  await assert.rejects(createThinkTogetherGuard(async () => ({ ok: false, json: async () => ({}) }))(auth, packet(1)), /authorized/)
  await assert.rejects(createThinkTogetherGuard(async () => { throw new Error('offline') })(auth, packet(0, 2)))
})
