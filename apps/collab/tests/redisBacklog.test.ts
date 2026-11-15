import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ACK_DIRTY_BOARD, createRedisBoardBacklog, DIRTY_BOARDS_KEY } from '../src/redisBacklog.js'

const board = 'board_87654321-4321-4321-4321-cba987654321'

function redis() {
  const values = new Map<string, Buffer>()
  return {
    values,
    async hset(key: string, field: string, value: Buffer) {
      assert.equal(key, DIRTY_BOARDS_KEY)
      values.set(field, value)
    },
    async hkeys(key: string) { assert.equal(key, DIRTY_BOARDS_KEY); return [...values.keys()] },
    async hgetBuffer(key: string, field: string) { assert.equal(key, DIRTY_BOARDS_KEY); return values.get(field) ?? null },
    calls: [] as unknown[][],
    async eval(...args: unknown[]) { this.calls.push(args) },
  }
}

test('dirty journal uses a nonexpiring hash and preserves binary snapshots on recovery', async () => {
  const client = redis()
  const backlog = createRedisBoardBacklog(() => client, 8)
  const state = Uint8Array.of(0, 255, 13, 10)
  await backlog.save(board, state)
  const recovered = []
  for await (const row of backlog.load()) recovered.push(row)
  assert.deepEqual(recovered, [[board, state]])
  await backlog.acknowledge(board, state)
  assert.deepEqual(client.calls, [[ACK_DIRTY_BOARD, 1, DIRTY_BOARDS_KEY, board, Buffer.from(state)]])
})

test('invalid names, empty snapshots and oversized snapshots cannot enter the journal', async () => {
  const client = redis()
  const backlog = createRedisBoardBacklog(() => client, 8)
  await assert.rejects(backlog.save('board_other', Uint8Array.of(1)), /Invalid/)
  await assert.rejects(backlog.save(board, new Uint8Array()), /Invalid/)
  await assert.rejects(backlog.save(board, new Uint8Array(9)), /Invalid/)
  assert.equal(client.values.size, 0)
})

test('one malformed recovered entry cannot strand other valid dirty boards', async () => {
  const client = redis()
  client.values.set('board_invalid', Buffer.from([1]))
  client.values.set(board, Buffer.from([2]))
  const backlog = createRedisBoardBacklog(() => client, 8)
  const recovered = []
  for await (const row of backlog.load()) recovered.push(row)
  assert.deepEqual(recovered, [[board, Uint8Array.of(2)]])
})
