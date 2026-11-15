import type { BoardBacklog } from './persistence.js'

export const DIRTY_BOARDS_KEY = 'collab:dirty:ydocs'
export const ACK_DIRTY_BOARD = `
if redis.call('HGET', KEYS[1], ARGV[1]) == ARGV[2] then
  return redis.call('HDEL', KEYS[1], ARGV[1])
end
return 0
`

type BacklogRedis = {
  hset(key: string, field: string, value: Buffer): Promise<unknown>
  hkeys(key: string): Promise<string[]>
  hgetBuffer(key: string, field: string): Promise<Buffer | null>
  eval(script: string, keyCount: number, ...args: (string | Buffer)[]): Promise<unknown>
}

export function createRedisBoardBacklog(
  getRedis: () => BacklogRedis,
  maxBytes: number,
  onInvalid?: (boardUuid: string, error: unknown) => void,
): BoardBacklog {
  function validate(boardUuid: string, state: Uint8Array) {
    if (!/^board_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(boardUuid)
      || !state.byteLength || state.byteLength > maxBytes) throw Error('Invalid dirty board snapshot')
  }
  return {
    async save(boardUuid, state) {
      validate(boardUuid, state)
      // Unlike the read cache, the dirty journal has no expiry.
      await getRedis().hset(DIRTY_BOARDS_KEY, boardUuid, Buffer.from(state))
    },
    async acknowledge(boardUuid, state) {
      validate(boardUuid, state)
      await getRedis().eval(ACK_DIRTY_BOARD, 1, DIRTY_BOARDS_KEY, boardUuid, Buffer.from(state))
    },
    async *load() {
      const redis = getRedis()
      for (const boardUuid of await redis.hkeys(DIRTY_BOARDS_KEY)) {
        const state = await redis.hgetBuffer(DIRTY_BOARDS_KEY, boardUuid)
        if (!state) continue // A live write may have been acknowledged meanwhile.
        try { validate(boardUuid, state) } catch (error) {
          onInvalid?.(boardUuid, error)
          continue
        }
        yield [boardUuid, new Uint8Array(state)]
      }
    },
  }
}
