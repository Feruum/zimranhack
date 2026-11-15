export type BoardBacklog = {
  save(boardUuid: string, state: Uint8Array): Promise<void>
  acknowledge(boardUuid: string, state: Uint8Array): Promise<void>
  load(): AsyncIterable<[string, Uint8Array]>
}

type Snapshot = { state: Uint8Array; journaled: boolean; journal: Promise<void> }
type Pending = {
  snapshot: Snapshot
  journalTail: Promise<void>
  timer?: unknown
  inFlight?: Promise<void>
  failures: number
}

/** One acknowledged write at a time per board; failed snapshots stay dirty. */
export function createBoardPersistence(options: {
  write(boardUuid: string, state: Uint8Array): Promise<void>
  delayMs: number
  maxRetryMs?: number
  backlog?: BoardBacklog
  onError?(boardUuid: string, error: unknown): void
  setTimer?(callback: () => Promise<void>, delay: number): unknown
  clearTimer?(timer: unknown): void
}) {
  const pending = new Map<string, Pending>()
  const readers = new Map<string, Set<{ state?: Uint8Array }>>()
  const setTimer = options.setTimer ?? ((callback, delay) => setTimeout(callback, delay))
  const clearTimer = options.clearTimer ?? (timer => clearTimeout(timer as ReturnType<typeof setTimeout>))
  const maxRetryMs = options.maxRetryMs ?? 30_000
  let stopped = false
  let recovering: Set<string> | undefined

  function cancel(entry: Pending) {
    if (entry.timer !== undefined) clearTimer(entry.timer)
    entry.timer = undefined
  }

  function journal(boardUuid: string, entry: Pending, snapshot: Snapshot): Promise<void> {
    snapshot.journal = entry.journalTail.catch(() => undefined).then(async () => {
      // Coalesce snapshots that were superseded before their journal turn.
      if (entry.snapshot !== snapshot) return
      await options.backlog?.save(boardUuid, snapshot.state)
      snapshot.journaled = true
    })
    entry.journalTail = snapshot.journal
    // A caller may leave the queue to retry; never create an unhandled rejection.
    void snapshot.journal.catch(error => options.onError?.(boardUuid, error))
    return snapshot.journal
  }

  function arm(boardUuid: string, entry: Pending, delay: number) {
    cancel(entry)
    if (stopped) return
    entry.timer = setTimer(async () => {
      entry.timer = undefined
      try { await attempt(boardUuid, entry) } catch { /* attempt schedules the retry */ }
    }, delay)
  }

  function attempt(boardUuid: string, entry: Pending): Promise<void> {
    if (entry.inFlight) return entry.inFlight
    cancel(entry)
    const snapshot = entry.snapshot
    entry.inFlight = (async () => {
      try {
        await snapshot.journal
        if (entry.snapshot !== snapshot) return
        if (!snapshot.journaled) await journal(boardUuid, entry, snapshot)
        await options.write(boardUuid, snapshot.state)
        // Redis uses exact-byte CAS: an old ack cannot erase a newer journal.
        await options.backlog?.acknowledge(boardUuid, snapshot.state)
        entry.failures = 0
        if (entry.snapshot === snapshot) pending.delete(boardUuid)
      } catch (error) {
        entry.failures++
        // A rejected journal promise must be retryable on the next attempt.
        if (!snapshot.journaled) snapshot.journal = Promise.resolve()
        options.onError?.(boardUuid, error)
        throw error
      } finally {
        entry.inFlight = undefined
        if (pending.get(boardUuid) === entry) {
          const delay = entry.failures
            ? Math.min(maxRetryMs, Math.max(1, options.delayMs) * 2 ** Math.min(entry.failures - 1, 10))
            : options.delayMs
          arm(boardUuid, entry, delay)
        }
      }
    })()
    return entry.inFlight
  }

  function schedule(boardUuid: string, state: Uint8Array): Promise<void> {
    if (stopped) throw Error('Board persistence is stopping')
    recovering?.add(boardUuid)
    const snapshot: Snapshot = { state: new Uint8Array(state), journaled: false, journal: Promise.resolve() }
    for (const reader of readers.get(boardUuid) ?? []) reader.state = snapshot.state
    let entry = pending.get(boardUuid)
    if (!entry) {
      entry = { snapshot, journalTail: Promise.resolve(), failures: 0 }
      pending.set(boardUuid, entry)
    } else entry.snapshot = snapshot
    const saved = journal(boardUuid, entry, snapshot)
    // Edits do not postpone an existing outage retry indefinitely.
    if (!entry.inFlight && (!entry.failures || entry.timer === undefined)) {
      arm(boardUuid, entry, options.delayMs)
    }
    return saved
  }

  async function flush(boardUuid: string): Promise<void> {
    let entry: Pending | undefined
    while ((entry = pending.get(boardUuid))) await attempt(boardUuid, entry)
  }

  return {
    schedule,
    flush,
    getPendingState(boardUuid: string): Uint8Array | undefined {
      const state = pending.get(boardUuid)?.snapshot.state
      return state && new Uint8Array(state)
    },
    async load(boardUuid: string, read: () => Promise<Uint8Array | null>): Promise<Uint8Array | null> {
      const dirty = pending.get(boardUuid)?.snapshot.state
      if (dirty) return new Uint8Array(dirty)
      const reader: { state?: Uint8Array } = {}
      let active = readers.get(boardUuid)
      if (!active) { active = new Set(); readers.set(boardUuid, active) }
      active.add(reader)
      try {
        const stored = await read()
        // Keep this witness only for the active read. The newer snapshot may
        // already have left pending after its DB acknowledgement.
        return reader.state ? new Uint8Array(reader.state) : stored
      } finally {
        active.delete(reader)
        if (!active.size) readers.delete(boardUuid)
      }
    },
    async recover(): Promise<void> {
      if (!options.backlog || recovering) return
      // Record live edits throughout recovery, including ones already acked.
      // A slow backlog read must never resurrect a snapshot older than those.
      recovering = new Set(pending.keys())
      try {
        for await (const [boardUuid, state] of options.backlog.load()) {
          if (!recovering.has(boardUuid)) await schedule(boardUuid, state)
        }
      } finally { recovering = undefined }
    },
    async shutdown(): Promise<number> {
      // One final bounded HTTP attempt per board. Any failure is safe to leave
      // only after its latest snapshot is acknowledged by the durable journal.
      stopped = true
      for (const entry of pending.values()) cancel(entry)
      await Promise.allSettled([...pending.keys()].map(flush))
      await Promise.all([...pending].map(async ([boardUuid, entry]) => {
        const snapshot = entry.snapshot
        await snapshot.journal.catch(() => undefined)
        if (!snapshot.journaled) await journal(boardUuid, entry, snapshot)
      }))
      if (pending.size && !options.backlog) throw Error('Dirty boards have no durable backlog')
      return pending.size
    },
  }
}
