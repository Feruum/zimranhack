import type { ConnectionAuth } from './accessRecheck.js'

/** Hocuspocus v4 wire frames start with a varString document address. */
export function isBoardMutation(message: Uint8Array): boolean {
  let offset = 0
  function uint(): number {
    let value = 0, multiplier = 1
    for (let count = 0; count < 8; count++) {
      const byte = message[offset++]
      if (byte === undefined) throw new Error('Malformed board message')
      value += (byte & 127) * multiplier
      if (!(byte & 128)) return value
      multiplier *= 128
    }
    throw new Error('Malformed board message')
  }
  const addressLength = uint()
  offset += addressLength
  if (offset >= message.byteLength) throw new Error('Malformed board message')
  const type = uint()
  // SyncReply (4) carries the same Yjs sync subprotocol as Sync (0).
  if (type === 0 || type === 4) return uint() !== 0
  return false
}

type ResponseLike = { ok: boolean; json(): Promise<unknown> }
export function createThinkTogetherGuard(
  fetchAccess: (auth: ConnectionAuth) => Promise<ResponseLike>,
) {
  return async (auth: ConnectionAuth | undefined, message: Uint8Array): Promise<void> => {
    if (!auth?.token || !auth.boardUuid) throw new Error('Room authentication required')
    const response = await fetchAccess(auth)
    if (!response.ok) throw new Error('No longer authorized for this room')
    const access = await response.json() as { managed?: unknown; can_write?: unknown }
    if (typeof access?.managed !== 'boolean') throw new Error('Room authorization unavailable')
    if (access.managed && access.can_write !== true && isBoardMutation(message)) {
      throw new Error('Board is locked during independent work and summary')
    }
  }
}
