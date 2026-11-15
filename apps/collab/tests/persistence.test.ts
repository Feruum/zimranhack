import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createBoardPersistence, type BoardBacklog } from '../src/persistence.js'

function timers() {
  let id = 0
  const tasks = new Map<number, { callback: () => unknown; delay: number }>()
  return {
    tasks,
    setTimeout(callback: () => unknown, delay: number) {
      tasks.set(++id, { callback, delay })
      return id
    },
    clearTimeout(timer: number) { tasks.delete(timer) },
    async next() {
      const next = tasks.entries().next().value
      assert.ok(next, 'a persistence retry must remain scheduled')
      tasks.delete(next[0])
      await next[1].callback()
    },
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}

function persistence(write: (state: Uint8Array) => Promise<{ ok: boolean; status: number }>, backlog?: BoardBacklog) {
  const clock = timers()
  const queue = createBoardPersistence({
    delayMs: 5000,
    backlog,
    setTimer: clock.setTimeout,
    clearTimer: timer => clock.clearTimeout(timer as number),
    async write(_boardUuid, state) {
      const response = await write(state)
      if (!response.ok) throw Error(`HTTP ${response.status}`)
    },
  })
  return { queue, clock }
}

const ok = { ok: true, status: 200 }

test('HTTP 503 retains the last dirty board and retries without another edit', async () => {
  const stored: number[][] = []
  const { queue, clock } = persistence(async state => {
    stored.push([...state])
    return stored.length === 1 ? { ok: false, status: 503 } : ok
  })
  queue.schedule('board', Uint8Array.of(1))
  await clock.next()
  assert.deepEqual([...queue.getPendingState('board') ?? []], [1])
  await clock.next()
  assert.deepEqual(stored, [[1], [1]])
  assert.equal(queue.getPendingState('board'), undefined)
  assert.equal(clock.tasks.size, 0)
})

test('a timeout retries the latest snapshot after an edit during the failed PUT', async () => {
  const first = deferred<{ ok: boolean; status: number }>()
  const started = deferred<void>()
  const stored: number[][] = []
  const { queue, clock } = persistence(async state => {
    stored.push([...state])
    if (stored.length === 1) {
      started.resolve()
      await first.promise
      throw Error('request timed out')
    }
    return ok
  })
  queue.schedule('board', Uint8Array.of(1))
  const flight = clock.next()
  await started.promise
  queue.schedule('board', Uint8Array.of(2))
  first.resolve(ok)
  await flight
  await clock.next()
  assert.deepEqual(stored, [[1], [2]])
  assert.equal(queue.getPendingState('board'), undefined)
})

test('a timeout keeps a retry scheduled when the board is no longer edited', async () => {
  let attempts = 0
  const { queue, clock } = persistence(async () => {
    if (++attempts === 1) throw Error('request timed out')
    return ok
  })
  queue.schedule('board', Uint8Array.of(1))
  await clock.next()
  assert.deepEqual([...queue.getPendingState('board') ?? []], [1])
  await clock.next()
  assert.equal(attempts, 2)
  assert.equal(queue.getPendingState('board'), undefined)
})

test('a newer edit waits for the older PUT so old completion cannot overwrite it', async () => {
  const first = deferred<{ ok: boolean; status: number }>()
  const started = deferred<void>()
  const stored: number[][] = []
  let active = 0, maxActive = 0
  const { queue, clock } = persistence(async state => {
    maxActive = Math.max(maxActive, ++active)
    if (state[0] === 1) {
      started.resolve()
      await first.promise
    }
    stored.push([...state])
    active--
    return ok
  })
  queue.schedule('board', Uint8Array.of(1))
  const flight = clock.next()
  await started.promise
  queue.schedule('board', Uint8Array.of(2))
  // Any ready timer must not start a concurrent request for this board.
  if (clock.tasks.size) await clock.next()
  first.resolve(ok)
  await flight
  while (clock.tasks.size) await clock.next()
  assert.equal(maxActive, 1)
  assert.deepEqual(stored, [[1], [2]])
  assert.equal(queue.getPendingState('board'), undefined)
})

function durableBacklog() {
  const states = new Map<string, Uint8Array>()
  return {
    states,
    async save(boardUuid: string, state: Uint8Array) { states.set(boardUuid, new Uint8Array(state)) },
    async acknowledge(boardUuid: string, state: Uint8Array) {
      const current = states.get(boardUuid)
      if (current && Buffer.from(current).equals(Buffer.from(state))) states.delete(boardUuid)
    },
    async *load(): AsyncIterable<[string, Uint8Array]> {
      for (const [board, state] of states) yield [board, new Uint8Array(state)]
    },
  }
}

test('an old PUT acknowledgement cannot delete a newer durable snapshot', async () => {
  const backlog = durableBacklog()
  const started = deferred<void>(), finish = deferred<void>()
  const { queue, clock } = persistence(async state => {
    if (state[0] === 1) { started.resolve(); await finish.promise }
    return ok
  }, backlog)
  await queue.schedule('board', Uint8Array.of(1))
  const flight = clock.next()
  await started.promise
  await queue.schedule('board', Uint8Array.of(2))
  finish.resolve()
  await flight
  assert.deepEqual([...backlog.states.get('board')!], [2])
  assert.deepEqual([...queue.getPendingState('board')!], [2])
  await clock.next()
  assert.equal(backlog.states.size, 0)
})

test('a restarted process drains the final edit without a client reconnect', async () => {
  const backlog = durableBacklog()
  const first = persistence(async () => ({ ok: false, status: 503 }), backlog)
  await first.queue.schedule('board', Uint8Array.of(3))
  await first.clock.next()
  const writes: number[][] = []
  const restarted = persistence(async state => { writes.push([...state]); return ok }, backlog)
  await restarted.queue.recover()
  assert.deepEqual([...restarted.queue.getPendingState('board')!], [3])
  await restarted.clock.next()
  assert.deepEqual(writes, [[3]])
  assert.equal(backlog.states.size, 0)
})

test('slow recovery cannot resurrect a snapshot after a newer live edit was acknowledged', async () => {
  const loaded = deferred<void>(), release = deferred<void>()
  const backlog: BoardBacklog = {
    async save() {}, async acknowledge() {},
    async *load() { loaded.resolve(); await release.promise; yield ['board', Uint8Array.of(1)] },
  }
  const { queue, clock } = persistence(async () => ok, backlog)
  const recovery = queue.recover()
  await loaded.promise
  await queue.schedule('board', Uint8Array.of(2))
  await clock.next()
  release.resolve()
  await recovery
  assert.equal(queue.getPendingState('board'), undefined)
  assert.equal(clock.tasks.size, 0)
})

test('shutdown leaves failed DB writes durably journaled and cancels retry timers', async () => {
  const backlog = durableBacklog()
  let attempts = 0
  const { queue, clock } = persistence(async () => { attempts++; return { ok: false, status: 503 } }, backlog)
  await queue.schedule('board', Uint8Array.of(4))
  assert.equal(await queue.shutdown(), 1)
  assert.equal(attempts, 1)
  assert.deepEqual([...backlog.states.get('board')!], [4])
  assert.equal(clock.tasks.size, 0)
})

test('shutdown waits for an in-flight write before flushing its newer snapshot', async () => {
  const started = deferred<void>(), finish = deferred<void>()
  const backlog = durableBacklog()
  const writes: number[][] = []
  const { queue, clock } = persistence(async state => {
    if (state[0] === 1) { started.resolve(); await finish.promise }
    writes.push([...state])
    return ok
  }, backlog)
  await queue.schedule('board', Uint8Array.of(1))
  const flight = clock.next()
  await started.promise
  await queue.schedule('board', Uint8Array.of(2))
  const stopping = queue.shutdown()
  finish.resolve()
  await flight
  assert.equal(await stopping, 0)
  assert.deepEqual(writes, [[1], [2]])
  assert.equal(clock.tasks.size, 0)
})

test('an agreed-note immediate flush uses the same serial writer as normal edits', async () => {
  const started = deferred<void>(), finish = deferred<void>()
  const { queue, clock } = persistence(async state => {
    if (state[0] === 1) { started.resolve(); await finish.promise }
    return ok
  })
  await queue.schedule('board', Uint8Array.of(1))
  const flight = clock.next()
  await started.promise
  await queue.schedule('board', Uint8Array.of(2))
  let acknowledged = false
  const note = queue.flush('board').then(() => { acknowledged = true })
  await Promise.resolve()
  assert.equal(acknowledged, false)
  finish.resolve()
  await flight
  await note
  assert.equal(acknowledged, true)
  assert.equal(queue.getPendingState('board'), undefined)
})

test('repeated outages back off to a capped delay instead of busy retrying', async () => {
  const { queue, clock } = persistence(async () => ({ ok: false, status: 503 }))
  await queue.schedule('board', Uint8Array.of(1))
  const delays: number[] = []
  for (let index = 0; index < 7; index++) {
    await clock.next()
    delays.push([...clock.tasks.values()][0].delay)
  }
  assert.deepEqual(delays, [5000, 10000, 20000, 30000, 30000, 30000, 30000])
})

test('a Redis journal failure retries before any DB write and stores the latest edit', async () => {
  const backlog = durableBacklog()
  const save = backlog.save
  let available = false, writes = 0
  backlog.save = async (board, state) => {
    if (!available) throw Error('Redis unavailable')
    await save(board, state)
  }
  const { queue, clock } = persistence(async () => { writes++; return ok }, backlog)
  await assert.rejects(queue.schedule('board', Uint8Array.of(1)), /Redis/)
  await clock.next()
  assert.equal(writes, 0)
  available = true
  await clock.next()
  assert.equal(writes, 1)
  assert.equal(backlog.states.size, 0)
})

test('journal saves are serialized when a newer edit arrives before an older save finishes', async () => {
  const backlog = durableBacklog()
  const save = backlog.save
  const started = deferred<void>(), finish = deferred<void>()
  backlog.save = async (board, state) => {
    if (state[0] === 1) { started.resolve(); await finish.promise }
    await save(board, state)
  }
  const { queue } = persistence(async () => ok, backlog)
  const older = queue.schedule('board', Uint8Array.of(1))
  await started.promise
  const newer = queue.schedule('board', Uint8Array.of(2))
  finish.resolve()
  await Promise.all([older, newer])
  assert.deepEqual([...backlog.states.get('board')!], [2])
})

test('a failed durable acknowledgement retains the snapshot after a successful DB PUT', async () => {
  const backlog = durableBacklog()
  const acknowledge = backlog.acknowledge
  let available = false, writes = 0
  backlog.acknowledge = async (board, state) => {
    if (!available) throw Error('Redis acknowledgement unavailable')
    await acknowledge(board, state)
  }
  const { queue, clock } = persistence(async () => { writes++; return ok }, backlog)
  await queue.schedule('board', Uint8Array.of(1))
  await clock.next()
  assert.deepEqual([...queue.getPendingState('board')!], [1])
  assert.deepEqual([...backlog.states.get('board')!], [1])
  available = true
  await clock.next()
  assert.equal(writes, 2)
  assert.equal(queue.getPendingState('board'), undefined)
  assert.equal(backlog.states.size, 0)
})

test('callers cannot mutate the snapshot queued for durable persistence', async () => {
  const writes: number[][] = []
  const { queue, clock } = persistence(async state => { writes.push([...state]); return ok })
  const state = Uint8Array.of(1)
  await queue.schedule('board', state)
  state[0] = 2
  queue.getPendingState('board')![0] = 3
  await clock.next()
  assert.deepEqual(writes, [[1]])
})

test('a slow document load returns a newer edit even after that edit has been acknowledged', async () => {
  const persisted = deferred<Uint8Array | null>()
  const { queue, clock } = persistence(async () => ok)
  const loading = queue.load('board', () => persisted.promise)
  await queue.schedule('board', Uint8Array.of(2))
  await clock.next()
  assert.equal(queue.getPendingState('board'), undefined)
  persisted.resolve(Uint8Array.of(1))
  assert.deepEqual(await loading, Uint8Array.of(2))
})

test('an unloaded dirty board is read from memory without a stale cache fetch', async () => {
  const { queue } = persistence(async () => ok)
  await queue.schedule('board', Uint8Array.of(2))
  const result = await queue.load('board', async () => { throw Error('stale cache must not be read') })
  assert.deepEqual(result, Uint8Array.of(2))
})
