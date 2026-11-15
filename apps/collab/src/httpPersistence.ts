/** Conditional PUTs also fence requests that finish server-side after timeout. */
export function createBoardSnapshotWriter(options: {
  apiUrl: string
  internalKey: string
  request(url: string, init?: RequestInit): Promise<Response>
  invalidate(boardUuid: string): Promise<unknown>
}) {
  return async (boardUuid: string, state: Uint8Array): Promise<void> => {
    const url = `${options.apiUrl}/api/v1/boards/${boardUuid}/ydoc`
    const snapshot = await options.request(url, { headers: { 'X-Internal-Key': options.internalKey } })
    const etag = snapshot.headers.get('ETag')
    // Only the version header is needed; release the potentially large body.
    await snapshot.body?.cancel()
    if (!snapshot.ok) throw Error(`Board version fetch returned HTTP ${snapshot.status}`)
    if (!etag || !/^"[0-9a-f]{64}"$/.test(etag)) throw Error('Board version ETag unavailable')
    const response = await options.request(url, {
      method: 'PUT',
      headers: {
        'X-Internal-Key': options.internalKey,
        'Content-Type': 'application/octet-stream',
        'If-Match': etag,
      },
      body: state.buffer.slice(state.byteOffset, state.byteOffset + state.byteLength) as ArrayBuffer,
    })
    await response.body?.cancel()
    // A conflict retains the queue's dirty state. Its retry gets a fresh ETag.
    if (!response.ok) throw Error(`Board persistence returned HTTP ${response.status}`)
    await options.invalidate(boardUuid)
  }
}
